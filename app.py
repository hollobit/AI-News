"""Local Telegram news and AI research intelligence workbench."""
import argparse
import json
import os
import re
import sqlite3
import time
import hashlib
import threading
import unicodedata
import io
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse, unquote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from classification import TOPICS, classify_message
from link_groups import build_link_groups, canonical_url, classify_link, extract_links
from semantic import AnalysisService, compare_mentions, input_hash
from knowledge_graph import build_graph_input, analyze_graph
from research import ResearchService
from source_enrichment import SourceService, attach_sources, source_map
from briefing import build_briefing
from mirofish_service import MiroFishService
from mirofish_runtime import runtime_status, source_files
from graph_rag import load_integrated_graph, answer_question
from keyword_index import (annotate_items, ensure_keyword_index, init_keyword_index,
                           keyword_discovery, mark_keyword_source_changed, public_item,
                           read_keyword_record)
from url_archive import init_archive, archive_message, backfill_archive, archived_rows, archived_link_rows

from news_repository import (unindexed_link_rows, hidden_link_rows, normalized_message, duplicate_message_keys, article_key, unique_articles, joined_articles, read_news)

ROOT = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
CLASSIFICATION_VERSION = "2-hierarchical-sources"
_LINK_CACHE = {"signature": None, "groups": []}
_LINK_CACHE_LOCK = threading.Lock()
_KEYWORD_SCHEMA_PATHS = set()
_KEYWORD_SCHEMA_LOCK = threading.Lock()
CONTENT_TYPES = {"news": "뉴스 기사", "paper": "논문 소개", "blog": "블로그·해설",
                 "social": "소셜 게시물", "tool": "도구·프로젝트", "other": "기타"}


def load_local_env(path=ROOT / ".env"):
    """Read only supported literal settings; never execute shell expressions."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        key = key.strip()
        if separator and key in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_IDS", "SSL_CERT_FILE", "NEWS_EXTERNAL_ANALYSIS_ENABLED"}:
            os.environ.setdefault(key, value.strip())


def connect(path):
    """Compatibility setup entry point for ingestion/CLI/tests."""
    from database import prepare_database
    return prepare_database(path, rebuild_articles, joined_articles, CLASSIFICATION_VERSION)


def index_message(db, row):
    db.execute("DELETE FROM articles WHERE chat_id=? AND message_id=?",
               (row["chat_id"], row["message_id"]))
    for index, item in enumerate(classify_message(dict(row))):
        db.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            row["chat_id"], row["message_id"], index, item["title"], item["excerpt"],
            item["text"], item["day"], item["date_basis"], item["topic"],
            item["source_url"], item["kind"],
        ))


def rebuild_articles(db):
    # Only derived rows change. Original Telegram messages remain intact.
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS article_reindex_history (
            id INTEGER PRIMARY KEY, chat_id TEXT, message_id INTEGER, parser_version TEXT,
            previous_json TEXT, replacement_json TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
        fields = ('title','excerpt','text','day','date_basis','topic','source_url','kind')
        for row in db.execute("SELECT * FROM news").fetchall():
            old = [dict(r) for r in db.execute(
                'SELECT * FROM articles WHERE chat_id=? AND message_id=? ORDER BY item_index',
                (row['chat_id'], row['message_id']))]
            parsed = classify_message(dict(row))
            if [{k: r[k] for k in fields} for r in old] == parsed:
                continue
            if old:
                db.execute("""INSERT INTO article_reindex_history
                    (chat_id,message_id,parser_version,previous_json,replacement_json)
                    VALUES(?,?,?,?,?)""", (row['chat_id'],row['message_id'],CLASSIFICATION_VERSION,
                    json.dumps(old,ensure_ascii=False),json.dumps(parsed,ensure_ascii=False)))
            index_message(db, row)
        db.execute("INSERT OR REPLACE INTO state VALUES ('classification_version', ?)",
                   (CLASSIFICATION_VERSION,))
        mark_keyword_source_changed(db)


def save_message(db, message):
    chat = message["chat"]
    body = (message.get("text") or message.get("caption") or "").strip()
    if chat.get("type") != "channel":
        return False
    # A caption removed by an edit must not leave a stale article behind.
    if not body:
        previous = db.execute("SELECT version FROM news WHERE chat_id=? AND message_id=?",
                              (str(chat["id"]), message["message_id"])).fetchone()
        if previous is None or previous[0] <= message.get("edit_date", message["date"]):
            archive_message(db, message)
        cursor = db.execute("DELETE FROM news WHERE chat_id=? AND message_id=? AND version<=?",
                            (str(chat["id"]), message["message_id"],
                             message.get("edit_date", message["date"])))
        db.execute("""DELETE FROM articles WHERE chat_id=? AND message_id=?
            AND NOT EXISTS (SELECT 1 FROM news WHERE chat_id=? AND message_id=?)""",
                   (str(chat["id"]), message["message_id"], str(chat["id"]), message["message_id"]))
        return bool(cursor.rowcount)
    published = datetime.fromtimestamp(message["date"], KST)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    title = lines[0][:120]
    excerpt = re.sub(r"\s+", " ", " ".join(lines[1:]) or body)
    excerpt = excerpt[:240] + ("…" if len(excerpt) > 240 else "")
    username = chat.get("username", "")
    chat_id = str(chat["id"])
    target = username if re.fullmatch(r"[A-Za-z0-9_]+", username) else (
        "c/" + chat_id[4:] if chat_id.startswith("-100") else "")
    url = f"https://t.me/{target}/{int(message['message_id'])}" if target else ""
    cursor = db.execute("""
        INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(chat_id,message_id) DO UPDATE SET
            channel=excluded.channel, title=excluded.title, excerpt=excluded.excerpt,
            text=excluded.text, url=excluded.url, published_at=excluded.published_at,
            day=excluded.day, version=excluded.version
        WHERE excluded.version >= news.version
    """, (chat_id, message["message_id"], chat.get("title", chat_id), title,
          excerpt, body, url, published.isoformat(), published.date().isoformat(),
          message.get("edit_date", message["date"])))
    if cursor.rowcount:
        archive_message(db, message)
        row = db.execute("SELECT * FROM news WHERE chat_id=? AND message_id=?",
                         (chat_id, message["message_id"])).fetchone()
        index_message(db, row)
    return bool(cursor.rowcount)


def process_updates(db, updates, allowed_channels):
    changed = False
    with db:
        for update in updates:
            message = update.get("channel_post") or update.get("edited_channel_post")
            if message and str(message["chat"]["id"]) in allowed_channels:
                saved=save_message(db,message)
                from collector_status import mark_received
                mark_received(db,message,datetime.now(KST).isoformat(),saved)
                changed = saved or changed
            # Commit the cursor with the articles, so a crash cannot skip data.
            db.execute("INSERT OR REPLACE INTO state VALUES ('offset', ?)",
                       (str(update["update_id"] + 1),))
        if changed:
            prior=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()
            pending=json.loads(prior[0]) if prior else {}
            pending['status']='extracting'
            db.execute("INSERT OR REPLACE INTO state VALUES ('collector_corpus_snapshot',?)",(json.dumps(pending),))
            mark_keyword_source_changed(db)
            ensure_keyword_index(db, lambda: joined_articles(db))
    return changed














def link_groups_for(db):
    rows = [dict(row) for row in joined_articles(db)]
    rows.extend(unindexed_link_rows(db, rows))
    rows.extend(hidden_link_rows(db, rows))
    signature = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with _LINK_CACHE_LOCK:
        if signature != _LINK_CACHE["signature"]:
            _LINK_CACHE["groups"] = build_link_groups(rows)
            _LINK_CACHE["signature"] = signature
        saved_sources = source_map(db)
        return [dict(group, source_context=saved_sources.get(group['canonical_url'], {}))
                for group in _LINK_CACHE["groups"]]


def find_link_group(db, group_id):
    return next((group for group in link_groups_for(db) if group["id"] == group_id), None)


def read_links(db, params, service=None):
    groups = link_groups_for(db)
    date = params.get("date", ["all"])[0]
    if date != "all":
        datetime.strptime(date, "%Y-%m-%d")
    query = params.get("q", [""])[0].strip()
    channel = params.get("channel", [""])[0]
    topic = params.get("topic", [""])[0]
    content_type = params.get("content_type", [""])[0]
    repeated = params.get("repeated", [""])[0] == "1"
    # Match date and channel on the same mention; show the full link history afterward.
    filtered = [group for group in groups if
                any((date == "all" or m["day"] == date) and (not channel or str(m["chat_id"]) == channel)
                    for m in group["mentions"])
                and (not repeated or group["distinct_days"] > 1)]
    if query:
        from news_search import filter_items
        filtered = filter_items(db, filtered, query, links=True)
    topic_counts = Counter(group["topic"] for group in filtered)
    filtered = [group for group in filtered if not topic or group["topic"] == topic]
    type_counts = Counter(group["content_type"] for group in filtered)
    filtered = [group for group in filtered if not content_type or group["content_type"] == content_type]
    page_size = 24
    total_pages = max(1, (len(filtered)+page_size-1)//page_size)
    page = min(max(1, int(params.get("page", ["1"])[0])), total_pages)
    summaries = []
    for group in filtered[(page-1)*page_size:page*page_size]:
        summary = {key: value for key, value in group.items() if key != "mentions"}
        summary["analysis_status"] = service.status(group)["status"] if service else "not_analyzed"
        summaries.append(summary)
    date_counts = Counter(day for group in groups for day in group["dates"])
    return {"groups": summaries, "total": len(filtered), "page": page, "page_size": page_size,
            "total_pages": total_pages, "date": date,
            "types": [{"id": key, "title": title, "count": type_counts[key]} for key, title in CONTENT_TYPES.items()],
            "topics": [{"id": key, "title": title, "count": topic_counts[key]} for key, title in TOPICS.items()],
            "dates": [{"date": day, "count": count} for day, count in sorted(date_counts.items(), reverse=True)],
            "channels": [dict(row) for row in db.execute(
                "SELECT chat_id AS id, MAX(channel) AS title FROM news GROUP BY chat_id ORDER BY title")]}


def read_briefing(db, params):
    news = read_news(db, params)
    groups = link_groups_for(db)
    # Only attach analyses whose evidence still matches; stale interpretations stay out of the briefing.
    analyses = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='link_analysis'").fetchone():
        analyses = {row["group_id"]: row for row in db.execute("SELECT * FROM link_analysis WHERE error IS NULL")}
    enriched = []
    for group in groups:
        saved = analyses.get(group["id"])
        if saved and saved["input_hash"] == input_hash(group):
            result = json.loads(saved["result"])
            enriched.append(dict(group, analysis=dict(result, status="complete")))
        else:
            enriched.append(group)
    briefing = build_briefing(news, enriched)
    featured_ids = {story["id"] for story in [briefing.get("lead"),
                    *briefing.get("highlights", [])] if story}
    for topic in briefing["topics"]:
        stories = topic.get("stories", [])
        additional = [story for story in stories if story["id"] not in featured_ids]
        previews = additional[:2] or stories[:2]
        topic["total_count"] = topic["count"]
        topic["preview_count"] = len(previews)
        topic["stories"] = previews
    briefing["timeline"] = [{key: story[key] for key in (
        "id", "title", "topic", "topic_title", "content_type", "content_type_title",
        "group_id", "first_seen", "last_seen", "change_type")}
        for story in briefing["timeline"]]
    briefing.update(dates=news["dates"], channels=news["channels"], types=news["types"],
                    filter_topics=news["topics"], demo=news["demo"],
                    keyword_discovery=news["keyword_discovery"],
                    raw_message_count=news["raw_message_count"])
    return briefing


def graph_input(db, params):
    rows = [dict(row) for row in joined_articles(db)]
    rows.extend(unindexed_link_rows(db, rows))
    rows.extend(hidden_link_rows(db, rows))
    from strategy import select_strategy_items
    rows = select_strategy_items(db, params, rows)
    return build_graph_input(rows, params)


def source_bundle():
    """Offer the running adaptation source without credentials or collected data."""
    output = io.BytesIO()
    files = list(ROOT.glob("*.py")) + [ROOT / "README.md", ROOT / "requirements.txt", ROOT / "requirements-dev.txt", ROOT / ".env.mirofish.example"]
    files.extend(source_files())
    files.extend(file for file in (ROOT / "integrations/mirofish").glob("*") if file.is_file())
    for directory in ("static", "tests", "vendor/mirofish"):
        files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix != ".pyc")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(set(files)):
            archive.write(file, file.relative_to(ROOT))
    return output.getvalue()


def simulation_news(db, payload, *, prepared=False):
    """Freeze exactly the user-selected news scope; credentials never enter it."""
    filters = payload.get("filters", {})
    allowed = {"date", "q", "topic", "channel", "keyword", "content_type", 'lens', 'terms', 'strategic_keyword', 'impact', 'sort', 'sector'}
    if not isinstance(filters, dict) or set(filters) - allowed:
        raise ValueError("시뮬레이션 뉴스 범위를 확인해 주세요.")
    if any(not isinstance(value, str) or len(value) > 2000 for value in filters.values()):
        raise ValueError("시뮬레이션 필터 값이 올바르지 않습니다.")
    params = {key: [value] for key, value in filters.items() if value}
    params.setdefault("date", ["all"])
    params.setdefault("sort", ["latest"])
    from strategy import select_strategy_items
    if prepared:
        from strategy_views import dataset, selected
        items = selected(db, params, dataset(db))
    else:
        items = select_strategy_items(db, params)
    if 'limit' in payload:
        limit = int(payload['limit'])
        if not 1 <= limit <= 100:
            raise ValueError('초안 뉴스 범위는 1~100개여야 합니다.')
        items = items[:limit]
    return items


def warm_strategy_views(path, stop, source_status):
    """Prepare local projections after startup; no service creation or model calls."""
    from projection_cache import revision_token
    from strategy_views import dataset, read_view
    from automation_runtime import view_status
    last_source = last_graph = None
    db_path = str(Path(path).resolve())
    while not stop.is_set():
        db = None
        try:
            from task_lifecycle import cancellable_db
            db = cancellable_db(sqlite3.connect(db_path, timeout=15))
            db.row_factory = sqlite3.Row
            source_revision = revision_token(db, ('source',))
            from dynamic_registry import list_registry
            warm_revision = (source_revision, list_registry(db)['version'])
            if source_revision is not None and warm_revision != last_source:
                view_status('preparing')
                from projection_worker import prepare
                prepare('strategy',db_path)
                if stop.is_set():break
                last_source = warm_revision
            if stop.is_set():
                break
            view_status('ready')
        except Exception as error:
            view_status('retrying', error)
            # Warming is optional; regular requests retain their normal error handling.
            print(f'화면 캐시 준비 지연: {type(error).__name__}', flush=True)
        finally:
            if db is not None:
                db.close()
        stop.wait(30)


def serve(path, port):
    from automation_runtime import server_lease
    with server_lease(path):
        _serve(path, port)


def _serve(path, port):
    from server_bootstrap import bootstrap
    bootstrap(path, port, {
        'rebuild_articles': rebuild_articles,
        'CLASSIFICATION_VERSION': CLASSIFICATION_VERSION,
        'ROOT': ROOT,
        'configured_channels': configured_channels,
        'find_link_group': find_link_group,
        'graph_input': graph_input,
        'read_briefing': read_briefing,
        'read_links': read_links,
        'simulation_news': simulation_news,
        'source_bundle': source_bundle,
        'warm_strategy_views': warm_strategy_views,
    })


def configured_channels():
    override = os.environ.get("TELEGRAM_CHANNEL_IDS")
    if override is not None:
        channels = {v.strip() for v in override.split(",") if v.strip()}
    else:
        try:
            config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
            channels = {str(channel["id"]) for channel in config["channels"]}
        except (OSError, ValueError, KeyError, TypeError):
            raise SystemExit("config.json의 channels 설정을 확인해 주세요.") from None
    if not channels or not all(re.fullmatch(r"-100\d+", c) for c in channels):
        raise SystemExit("수집 채널 ID는 -100… 형태여야 합니다. config.json 또는 TELEGRAM_CHANNEL_IDS를 확인해 주세요.")
    return channels


def collect(path):
    import fcntl
    lock_path=Path(str(Path(path).resolve())+'.collector.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('이 DB의 Telegram 수집기가 이미 실행 중입니다.')
        _collect(path)


def _collect(path):
    channels = configured_channels()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("채널 설정은 준비되었습니다. TELEGRAM_BOT_TOKEN에 수집용 봇 토큰을 설정해 주세요. README.md 참고.")
    db = connect(path)
    db.execute('PRAGMA busy_timeout=60000')
    if db.execute("SELECT 1 FROM state WHERE key='demo'").fetchone():
        raise SystemExit("샘플 DB에는 수집할 수 없습니다. --db data/news.sqlite3 를 사용해 주세요.")
    print(f"수집 대상: {', '.join(sorted(channels))}. 새 메시지를 기다립니다. 종료: Ctrl+C", flush=True)
    while True:
        channels=configured_channels()
        row = db.execute("SELECT value FROM state WHERE key='offset'").fetchone()
        payload = {"offset": int(row[0]) if row else 0, "timeout": 30,
                   "allowed_updates": ["channel_post", "edited_channel_post"]}
        request = Request(f"https://api.telegram.org/bot{token}/getUpdates",
                          data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=40) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise SystemExit("Telegram API 요청이 거절되었습니다. 봇 설정을 확인해 주세요.")
            checked=datetime.now(KST).isoformat()
            changed=process_updates(db, result["result"], channels)
            with db:
                db.execute("INSERT OR REPLACE INTO state VALUES ('collector_last_success',?)", (checked,))
                from collector_status import mark_checked
                mark_checked(db,channels,checked)
            extraction=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()
            if changed or not extraction or json.loads(extraction[0]).get('status')!='complete':
                from collector_status import finish_extraction
                previous=json.loads(extraction[0]) if extraction else {}
                with db:
                    db.execute("INSERT OR REPLACE INTO state VALUES ('collector_corpus_snapshot',?)",
                               (json.dumps(dict(previous,status='extracting',checked_at=checked)),))
                finish_extraction(db,channels,checked)
        except HTTPError as error:
            if error.code in (401, 403, 404, 409):
                raise SystemExit(f"Telegram HTTP {error.code}: 토큰, 권한, 기존 webhook 또는 중복 수집 프로세스를 확인해 주세요.") from None
            delay = 10
            if error.code == 429:
                try:
                    delay = max(1, int(json.load(error).get("parameters", {}).get("retry_after", 10)))
                except (ValueError, TypeError):
                    pass
            print(f"Telegram HTTP {error.code}. {delay}초 뒤 재시도합니다.", flush=True)
            time.sleep(delay)
        except sqlite3.OperationalError as error:
            if not any(word in str(error).lower() for word in ('locked','busy')):raise
            db.rollback()
            print('분석 작업의 DB 저장을 기다린 뒤 수집·추출을 다시 확인합니다.',flush=True)
            time.sleep(2)
        except (URLError, TimeoutError, ConnectionError):
            # Exceptions may contain the token URL. Never print them.
            print("연결이 끊어졌습니다. 10초 뒤 재시도합니다.", flush=True)
            time.sleep(10)


def seed(path):
    db = connect(path)
    if db.execute("SELECT 1 FROM news LIMIT 1").fetchone():
        raise SystemExit("이미 메시지가 있는 DB입니다. 비어 있는 별도 DB를 사용해 주세요.")
    samples = [
        ("기술 브리핑", "[샘플] Hermes의 아침 기술 브리핑\n밤사이 수집한 기술 소식을 이곳에서 확인합니다. 실제 채널을 연결하면 Hermes가 보낸 메시지가 날짜별로 쌓입니다.\n이 내용은 화면 확인용 예시이며 실제 뉴스가 아닙니다."),
        ("산업 동향", "[샘플] 여러 채널의 소식을 한 페이지에서\n서로 다른 채널의 메시지를 발행 시각순으로 모읍니다. 채널 필터를 선택하면 원하는 출처의 메시지만 읽을 수 있습니다."),
        ("기술 브리핑", "[샘플] 짧게 훑고, 필요하면 전문 읽기\n목록에는 메시지의 앞부분을 발췌해서 보여줍니다. 전문을 펼쳐 자세히 읽거나 텔레그램 원문으로 돌아갈 수 있습니다. AI가 생성한 요약은 아닙니다."),
    ]
    with db:
        db.execute("INSERT OR REPLACE INTO state VALUES ('demo','1')")
        for days_ago in range(4):
            for i, (channel, body) in enumerate(samples):
                stamp = datetime.now(KST).replace(hour=9+i*2, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
                save_message(db, {"chat": {"id": -100100-i%2, "title": channel, "type": "channel"},
                                  "message_id": days_ago*10+i+1, "date": int(stamp.timestamp()), "text": body})
        db.execute("UPDATE news SET url=''")
        mark_keyword_source_changed(db)
        ensure_keyword_index(db, lambda: joined_articles(db))
    db.close()
    print(f"샘플 메시지를 저장했습니다: {path}")


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "collect", "demo", "reclassify"])
    parser.add_argument("--db", default=str(ROOT / "data/news.sqlite3"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            serve(args.db, args.port)
        elif args.command == "collect":
            collect(args.db)
        elif args.command == "reclassify":
            db = connect(args.db)
            rebuild_articles(db)
            print(f"원문 {db.execute('SELECT COUNT(*) FROM news').fetchone()[0]}건을 다시 분류했습니다.")
            db.close()
        else:
            seed(args.db)
    except KeyboardInterrupt:
        print("\n종료했습니다.")


if __name__ == "__main__":
    main()
