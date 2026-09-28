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

ROOT = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
CLASSIFICATION_VERSION = "1"
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
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS news (
            chat_id TEXT, message_id INTEGER, channel TEXT, title TEXT,
            excerpt TEXT, text TEXT, url TEXT, published_at TEXT, day TEXT,
            version INTEGER, PRIMARY KEY(chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS news_day ON news(day);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS articles (
            chat_id TEXT, message_id INTEGER, item_index INTEGER,
            title TEXT, excerpt TEXT, text TEXT, day TEXT, date_basis TEXT,
            topic TEXT, source_url TEXT, kind TEXT,
            PRIMARY KEY(chat_id, message_id, item_index)
        );
        CREATE INDEX IF NOT EXISTS articles_day_topic ON articles(day, topic);
    """)
    init_archive(db)
    database_key = str(Path(path).resolve())
    with _KEYWORD_SCHEMA_LOCK:
        if database_key not in _KEYWORD_SCHEMA_PATHS:
            init_keyword_index(db)
            db.commit()
            _KEYWORD_SCHEMA_PATHS.add(database_key)
    if not db.execute("SELECT 1 FROM url_archive_meta WHERE key='legacy_news_backfill_complete'").fetchone():
        with db:
            db.execute("BEGIN IMMEDIATE")
            backfill_archive(db)
    version = db.execute("SELECT value FROM state WHERE key='classification_version'").fetchone()
    if version is None or version[0] != CLASSIFICATION_VERSION:
        rebuild_articles(db)
    ensure_keyword_index(db, lambda: joined_articles(db))
    return db


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
        db.execute("DELETE FROM articles")
        for row in db.execute("SELECT * FROM news").fetchall():
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


def normalized_message(text):
    """Ignore cosmetic spacing, preserving case, punctuation and content dates."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def duplicate_message_keys(db):
    """Keep the first copy per channel, regardless of Telegram posting date."""
    seen, duplicates = set(), set()
    hidden = {}
    for url in db.execute("SELECT chat_id,message_id,original_url,title FROM archived_urls WHERE active=1 AND entity_type='text_link'"):
        hidden.setdefault((url["chat_id"], url["message_id"]), set()).add((url["original_url"], url["title"]))
    for row in db.execute("SELECT chat_id,message_id,text FROM news ORDER BY published_at,message_id,chat_id"):
        key = (row["chat_id"], normalized_message(row["text"]),
               tuple(sorted(hidden.get((row["chat_id"], row["message_id"]), set()))))
        if key in seen:
            duplicates.add((row["chat_id"], row["message_id"]))
        else:
            seen.add(key)
    return duplicates


def article_key(item):
    """Merge identical same-day descriptions, never URL-only updates."""
    text = item["text"]
    for url in sorted(extract_links(text), key=len, reverse=True):
        text = text.replace(url, canonical_url(url) or url)
    return item["day"], normalized_message(text), tuple(item.get("embedded_urls", []))


def unique_articles(rows):
    grouped = {}
    for row in rows:
        item = dict(row)
        key = article_key(item)
        source = {name: item[name] for name in ("chat_id", "message_id", "channel", "url")}
        if key not in grouped:
            item.update(classify_link(item["source_url"], item["title"], item["text"]))
            item["topic_title"] = TOPICS.get(item["topic"], TOPICS["general"])
            item["sources"] = [source]
            grouped[key] = item
        elif source not in grouped[key]["sources"]:
            grouped[key]["sources"].append(source)
    for item in grouped.values():
        item["source_count"] = len(item["sources"])
    return list(grouped.values())


def joined_articles(db):
    duplicates = duplicate_message_keys(db)
    rows = db.execute("""SELECT a.*, n.channel, n.url, n.published_at,
        n.day AS telegram_day FROM articles a JOIN news n
        ON a.chat_id=n.chat_id AND a.message_id=n.message_id
        ORDER BY a.day DESC, n.published_at DESC, a.message_id DESC, a.item_index""").fetchall()
    hidden = {}
    for url in db.execute("SELECT chat_id,message_id,original_url FROM archived_urls WHERE active=1 AND entity_type='text_link'"):
        hidden.setdefault((url["chat_id"], url["message_id"]), set()).add(url["original_url"])
    from reach_pipeline import external_rows
    return attach_sources(db, [dict(row, embedded_urls=sorted(hidden.get((row["chat_id"], row["message_id"]), set())))
            for row in rows if (row["chat_id"], row["message_id"]) not in duplicates]+external_rows(db))


def read_news(db, params):
    rows = joined_articles(db)
    dates_count = Counter(item["day"] for item in unique_articles(rows))
    dates = [{"date": day, "count": count} for day, count in sorted(dates_count.items(), reverse=True)]
    day = params.get("date", [dates[0]["date"] if dates else datetime.now(KST).date().isoformat()])[0]
    if day != "all":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
        datetime.strptime(day, "%Y-%m-%d")
    query = params.get("q", [""])[0].strip().casefold()
    channel = params.get("channel", [""])[0]
    topic = params.get("topic", [""])[0]
    content_type = params.get("content_type", [""])[0]
    keyword = params.get("keyword", [""])[0]
    if keyword and not re.fullmatch(r"[0-9a-f]{16}", keyword):
        raise ValueError("키워드 ID 형식이 올바르지 않습니다.")
    filtered = annotate_items(db, unique_articles(
        row for row in rows if (day == "all" or row["day"] == day)
        and (not channel or str(row["chat_id"]) == channel)
        and (not query or query in row["text"].casefold())))
    if keyword:
        filtered = [item for item in filtered
                    if keyword in item["_all_keyword_ids"]]
    counts = Counter(item["topic"] for item in filtered)
    type_counts = Counter(item["content_type"] for item in filtered if not topic or item["topic"] == topic)
    topics = [{"id": key, "title": title, "count": counts[key]} for key, title in TOPICS.items()]
    channels = [dict(row) for row in db.execute(
        "SELECT chat_id AS id, MAX(channel) AS title FROM news GROUP BY chat_id ORDER BY title")]
    if any(r.get('source_origin')=='external_watch' for r in rows):channels.append({'id':-9900,'title':'외부 정기 관측'})
    items = [item for item in filtered if (not topic or item["topic"] == topic)
             and (not content_type or item["content_type"] == content_type)]
    discovery = keyword_discovery(db, items, day, keyword)
    items = [public_item(item) for item in items]
    return {"date": day, "dates": dates, "channels": channels,
            "topics": topics, "types": [{"id": key, "title": title, "count": type_counts[key]}
                                           for key, title in CONTENT_TYPES.items()],
            "items": items,
            "keyword_discovery": discovery,
            "raw_message_count": db.execute("SELECT COUNT(*) FROM news").fetchone()[0],
            "demo": db.execute("SELECT 1 FROM state WHERE key='demo'").fetchone() is not None}


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


def unindexed_link_rows(db, indexed_rows):
    """Keep source-only fragments that Telegram message splitting left outside articles."""
    represented = {}
    for row in indexed_rows:
        key = (str(row["chat_id"]), row["message_id"])
        represented.setdefault(key, set()).update(canonical_url(url) for url in
                                                 extract_links(row["text"]) + [row["source_url"]] if url)
    extras = []
    duplicates = duplicate_message_keys(db)
    for raw in db.execute("SELECT * FROM news"):
        key = (str(raw["chat_id"]), raw["message_id"])
        if key in duplicates:
            continue
        known = represented.setdefault(key, set())
        for url in extract_links(raw["text"]):
            canonical = canonical_url(url)
            if not canonical or canonical in known:
                continue
            known.add(canonical)
            lines = raw["text"].splitlines()
            index = next((i for i, line in enumerate(lines) if url in line), 0)
            previous = lines[index-1].strip() if index else ""
            # Do not fabricate the missing article title or inherit another item's text.
            if not previous or extract_links(previous) or re.fullmatch(r"[-━=\s]+", previous):
                previous = "원문에 남아 있는 링크 조각"
            title = previous[:160]
            extras.append({"chat_id": raw["chat_id"], "message_id": raw["message_id"],
                           "item_index": -int(hashlib.sha256(canonical.encode()).hexdigest()[:12], 16)-1,
                           "title": title, "excerpt": "개별 뉴스로 분리되지 않은 원문 링크입니다. 텔레그램 출처에서 문맥을 확인할 수 있습니다.",
                           "text": title + "\n" + url, "day": raw["day"], "date_basis": "telegram",
                           "topic": "general", "source_url": url, "kind": "briefing",
                           "channel": raw["channel"], "url": raw["url"], "published_at": raw["published_at"],
                           "telegram_day": raw["day"]})
    return extras


def hidden_link_rows(db, existing_rows):
    duplicates = duplicate_message_keys(db)
    known = {}
    for row in existing_rows:
        key = (str(row["chat_id"]), row["message_id"])
        known.setdefault(key, set()).update(canonical_url(url) for url in
            extract_links(row["text"]) + [row["source_url"]] if url)
    result = []
    for row in archived_link_rows(db, hidden_only=True):
        key = (str(row["chat_id"]), row["message_id"])
        url = canonical_url(row["source_url"])
        if key in duplicates or url in known.get(key, set()):
            continue
        known.setdefault(key, set()).add(url)
        result.append(row)
    return result


def find_link_group(db, group_id):
    return next((group for group in link_groups_for(db) if group["id"] == group_id), None)


def read_links(db, params, service=None):
    groups = link_groups_for(db)
    date = params.get("date", ["all"])[0]
    if date != "all":
        datetime.strptime(date, "%Y-%m-%d")
    query = params.get("q", [""])[0].strip().casefold()
    channel = params.get("channel", [""])[0]
    topic = params.get("topic", [""])[0]
    content_type = params.get("content_type", [""])[0]
    repeated = params.get("repeated", [""])[0] == "1"
    # Match date and channel on the same mention; show the full link history afterward.
    filtered = [group for group in groups if
                any((date == "all" or m["day"] == date) and (not channel or str(m["chat_id"]) == channel)
                    for m in group["mentions"])
                and (not repeated or group["distinct_days"] > 1)
                and (not query or query in group["canonical_url"].casefold()
                     or any(query in m["text"].casefold() for m in group["mentions"]))]
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
            db = sqlite3.connect(db_path, timeout=15)
            db.row_factory = sqlite3.Row
            source_revision = revision_token(db, ('source',))
            from dynamic_registry import list_registry
            warm_revision = (source_revision, list_registry(db)['version'])
            if source_revision is not None and warm_revision != last_source:
                view_status('preparing')
                dataset(db)
                if stop.is_set():
                    break
                read_view(db, {'view': ['overview'], 'date': ['all']}, source_status())
                last_source = warm_revision
            if stop.is_set():
                break
            graph_revision = revision_token(db)
            if graph_revision is not None and graph_revision != last_graph:
                graph = load_integrated_graph(db, {}, for_retrieval=True)
                from graph_retrieval import get_index
                get_index(graph)
                last_graph = graph_revision
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
    # LaunchAgents and long-running workers may change the process working
    # directory. Resolve once so every service, including MiroFish shutdown,
    # uses the same database file rather than a relative path.
    path = str(Path(path).resolve())
    connect(path).close()
    from automation_runtime import interrupted_runs
    recovery_candidates = interrupted_runs(path)
    analysis_service = AnalysisService(path)
    graph_service = AnalysisService(path, analyzer=analyze_graph, table="graph_analysis")
    research_service = ResearchService(path)
    source_service = SourceService(path)
    from strategic_workflow import WorkflowService
    workflow_service = WorkflowService(path, sources=source_service)
    from recursive_improvement import RecursiveImprovementService
    from improvement_selection import select_improvement_news
    def improvement_selector(settings, tasks, seen_ids):
        db = connect(path)
        try:
            from dynamic_strategy import discovery_followups
            return select_improvement_news(db, settings, list(tasks)+discovery_followups(db), seen_ids)
        finally:
            db.close()
    improvement_service = RecursiveImprovementService(path, workflow_service, improvement_selector)
    def public_improvement(run):
        if not run:
            return run
        result = dict(run)
        result['rounds'] = [dict(round_data, snapshot={key: value for key, value in (round_data.get('snapshot') or {}).items()
                            if key in ('identities', 'new_document_count', 'same_snapshot_retry')})
                            for round_data in run.get('rounds', [])[-24:]]
        result['tasks'] = run.get('tasks', [])[-100:]
        result['rules'] = run.get('rules', [])[-32:]
        result['history_scope'] = '최근 24회차·후속 과제 100개·개선 규칙 32개 표시; 전체 이력은 DB에 보존'
        result['coverage'] = {k: v for k, v in (run.get('coverage') or {}).items() if k != 'corpus_ids'}
        return result
    def improvement_catalog():
        from improvement_memory import list_catalog
        db = connect(path)
        try:
            return list_catalog(db, limit=100)
        finally:
            db.close()
    from arxiv_papers import PaperService, read_papers, paper
    from paper_analysis import PaperAnalysisService
    paper_service = PaperService(path)
    paper_analysis_service = PaperAnalysisService(path)
    from paper_pipeline import PaperPipeline
    paper_pipeline = PaperPipeline(path,paper_service,paper_analysis_service)
    from bulk_baseline import BulkBaselineService
    def baseline_selector():
        from improvement_selection import all_corpus_items
        db = connect(path)
        try:
            return all_corpus_items(db)
        finally:
            db.close()
    baseline_service = BulkBaselineService(path, baseline_selector)
    simulation_service = MiroFishService(path, readiness=runtime_status)
    from strategic_hub import StrategicHub
    intelligence_service = StrategicHub(path, simulation=simulation_service)
    from reach_pipeline import ReachPipeline
    reach_pipeline = ReachPipeline(path)
    from graph_questions import GraphQuestions
    question_service = GraphQuestions(path,enabled=graph_service.enabled,enricher=reach_pipeline)
    question_service.warm()
    from observatory_runtime import ObservatoryRuntime

    from agent_reach_service import AgentReachService
    reach_service = AgentReachService(path)
    from corpus_status import CorpusStatus
    corpus_status = CorpusStatus(path)
    corpus_status.get()
    def observatory_status():
        from collector_status import channel_status
        from status_views import status_response
        with sqlite3.connect(path,timeout=2) as db:
            db.row_factory=sqlite3.Row
            collector=channel_status(db,configured_channels(),ROOT/'config.json')
            base=status_response(db,'baseline',limit=1)
            deep=status_response(db,'improvement',limit=1)
        return dict(collector=collector,base=base,deep=deep,corpus=corpus_status.get(),sources=source_service.status())
    observatory_service = ObservatoryRuntime(path,status_loader=observatory_status)
    observatory_service.request();observatory_service.status()

    from article_explanations import ArticleExplanations
    article_service = ArticleExplanations(path,observatory_service)
    from knowledge_wiki import KnowledgeWiki
    wiki_service = KnowledgeWiki(path)

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, result, status=200, *, etag=None):
            if etag is not None and status == 200:
                tag = '"' + str(etag).strip('"') + '"'
                matches = [value.strip().removeprefix('W/') for value in self.headers.get('If-None-Match', '').split(',')]
                if tag in matches or '*' in matches:
                    self.send_response(304)
                    self.send_header('ETag', tag)
                    self.send_header('Cache-Control', 'private, no-cache')
                    self.end_headers()
                    return
            content = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "private, no-cache" if etag is not None else "no-store")
            if etag is not None:
                self.send_header('ETag', '"' + str(etag).strip('"') + '"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            route = urlparse(self.path)
            status = 200
            params = parse_qs(route.query)
            if route.path == '/api/wiki/network':
                try:self.send_json(wiki_service.network(identity=params.get('id',[''])[0][:200],query=params.get('q',[''])[0][:200],layer=params.get('layer',['all'])[0],limit=params.get('limit',['100'])[0],source_url=params.get('source_url',[''])[0][:2048],paper_id=params.get('paper_id',[''])[0][:100]))
                except (ValueError,TypeError):self.send_json({'error':'지식 연결 조회 조건을 확인하세요.'},400)
                except sqlite3.OperationalError:self.send_json({'error':'저장소 갱신 중입니다.'},503)
                return
            if route.path in ('/api/wiki/export','/api/wiki/vault'):
                try:
                    vault = route.path.endswith('/vault')
                    content = wiki_service.vault() if vault else wiki_service.export().encode('utf-8')
                except sqlite3.OperationalError:
                    self.send_json({'error': '저장소가 갱신 중입니다.'}, 503)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip' if vault else 'text/markdown; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="news-wiki.'+('zip' if vault else 'md')+'"')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if route.path == '/api/wiki':
                try:
                    self.send_json(wiki_service.get(params.get('id', [None])[0], params.get('q', [''])[0][:200], params.get('source_url', [''])[0][:2048]))
                except sqlite3.OperationalError:
                    self.send_json({'error': '위키 저장소가 갱신 중입니다. 잠시 후 다시 조회해 주세요.'}, 503)
                return
            if route.path in ('/wiki', '/wiki.js', '/wiki.css','/knowledge','/wiki-network.js','/wiki-network-3d.js','/wiki-network.css','/three.module.js','/three.core.js'):
                name = 'wiki.html' if route.path == '/wiki' else 'wiki-network.html' if route.path=='/knowledge' else route.path[1:]
                content = (ROOT / 'static' / name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', {'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css'}[name.rsplit('.',1)[-1]] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(content)
                return
            if route.path == '/paper-context.js':
                content=(ROOT/'static'/'paper-context.js').read_bytes()
                self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            if route.path == '/api/article-explanations':
                try:self.send_json(article_service.get(params.get('url',[''])[0]))
                except ValueError as error:self.send_json({'error':str(error)},400)
                return
            if route.path in ('/article','/article.js'):
                name='article.html' if route.path=='/article' else 'article.js'
                content=(ROOT/'static'/name).read_bytes()
                self.send_response(200);self.send_header('Content-Type',('text/html' if name.endswith('html') else 'application/javascript')+'; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            if route.path in ('/sources','/sources.js','/sources.css'):
                name='sources.html' if route.path=='/sources' else route.path[1:]
                content=(ROOT/'static'/name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',{'html':'text/html','js':'application/javascript','css':'text/css'}[name.rsplit('.',1)[1]]+'; charset=utf-8')
                self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            if route.path == '/api/agent-reach':
                self.send_json(reach_service.status()); return
            if route.path == '/api/corpus/status':
                try:self.send_json(corpus_status.get())
                except sqlite3.OperationalError:self.send_json({'error':'수집량 집계 중입니다.'},503)
                return
            if route.path == '/api/agent-reach/pipeline':
                self.send_json(reach_pipeline.status());return
            if route.path == '/api/agent-reach/passages':
                from source_store import search
                from link_groups import canonical_url
                query=params.get('q',[''])[0][:500];url=canonical_url(params.get('url',[''])[0])
                with sqlite3.connect(path,timeout=2) as db:self.send_json({'evidence':search(db,query,urls=[url])})
                return
            if route.path == '/api/agent-reach/source':
                from source_store import detail
                with sqlite3.connect(path) as db:self.send_json(detail(db,params.get('url',[''])[0]))
                return
            if route.path.startswith('/api/agent-reach/tasks/'):
                result=reach_pipeline.get(route.path.rsplit('/',1)[-1]);self.send_json(result or {'error':'작업 없음'},200 if result else 404);return
            if route.path.startswith('/api/agent-reach/jobs/'):
                result=reach_service.get(route.path.rsplit('/',1)[-1])
                self.send_json(result or {'error':'읽기 작업을 찾을 수 없습니다.'},200 if result else 404); return
            if route.path.startswith('/api/graph/answers/'):
                try:
                    result=question_service.get(route.path.rsplit('/',1)[-1])
                    self.send_json(result or {'error':'분석 작업을 찾을 수 없습니다.'},200 if result else 404)
                except (RuntimeError,sqlite3.OperationalError):
                    self.send_json({'error':'근거 갱신 중입니다. 잠시 후 다시 조회해 주세요.'},503)
                return
            if route.path=='/api/collector/channels':
                from collector_status import channel_status
                with sqlite3.connect(path,timeout=.25) as db:
                    db.row_factory=sqlite3.Row
                    self.send_json(channel_status(db,configured_channels(),ROOT/'config.json'))
                return
            if route.path == '/api/intelligence':
                try:
                    result = intelligence_service.get(params.get('view',['overview'])[0],params)
                    missing = 'item' in result and result['item'] is None
                    self.send_json(result if not missing else {'error':'항목을 찾을 수 없습니다.'},404 if missing else 200)
                except (ValueError,TypeError) as error:
                    self.send_json({'error':str(error)},400)
                except sqlite3.OperationalError:
                    self.send_json({'error':'전략 자료를 준비하고 있습니다. 잠시 후 다시 조회해 주세요.'},503)
                return
            if route.path == '/api/observatory/events':
                # Short streams bound per-client resources. EventSource reconnects
                # with Last-Event-ID; only changed persisted records are sent.
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    self.send_json({'error':'동일 사이트에서만 연결할 수 있습니다.'},403);return
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Connection','close');self.end_headers()
                import time
                last=self.headers.get('Last-Event-ID','')
                try:
                    for _ in range(10):
                        try:
                            payload=observatory_service.events()
                            if payload['version']!=last:
                                last=payload['version']
                                message='id: '+last+'\nevent: processing\ndata: '+json.dumps(payload,ensure_ascii=False)+'\n\n'
                            else:message=': heartbeat\n\n'
                        except sqlite3.OperationalError:message=': database-busy\n\n'
                        self.wfile.write(message.encode());self.wfile.flush();time.sleep(2)
                except (BrokenPipeError,ConnectionResetError):pass
                self.close_connection=True;return
            if route.path == '/api/observatory/status':
                self.send_json(observatory_service.status());return
            if route.path == '/api/observatory/history':
                try:self.send_json(observatory_service.events())
                except sqlite3.OperationalError:self.send_json({'error':'처리 기록 갱신 중입니다.'},503)
                return
            if route.path == '/api/observatory':
                try:
                    expanded=params.get('expand',['0'])[0].lower() in {'1','true','yes','expanded'}
                    result=observatory_service.request(int(params.get('window',['14'])[0]),expanded)
                    self.send_json(result,202 if result.get('status')=='preparing' else 200)
                except ValueError as error:self.send_json({'error':str(error)},400)
                return
            if route.path == '/api/strategy/topics':
                from dynamic_registry import list_registry
                from dynamic_strategy import dynamic_projection
                from strategy_views import dataset
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    data = dataset(db)
                    dynamic_projection(db, data['items'], data['morph'], data.get('revision'))
                    result = list_registry(db)
                    self.send_json(result, etag=str(result['version']))
                finally:
                    db.close()
                return
            if route.path == '/api/runtime/automation':
                from automation_runtime import runtime_status as automation_status
                self.send_json(automation_status())
                return
            if route.path == '/api/runtime/views':
                from projection_cache import cache_info
                self.send_json(cache_info())
                return
            if route.path == '/api/runtime/analysis':
                from llm_runtime import runtime_status as analysis_runtime_status
                self.send_json(analysis_runtime_status())
                return
            compact_match = re.fullmatch(r'/api/(baseline|improvement|workflows)(?:/([a-zA-Z0-9_-]+))?', route.path)
            if compact_match and params.get('view', [''])[0] == 'status':
                from status_views import status_response
                kind, run_id = compact_match.groups()
                db = sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True, timeout=15)
                try:
                    with baseline_service.lock:
                        active_workers = {baseline_service.active: baseline_service.active_batches} if baseline_service.active else {}
                    result = status_response(db, kind, run_id,
                        enabled=baseline_service.enabled if kind == 'baseline' else workflow_service.enabled,
                        active_workers=active_workers)
                    self.send_json(result if result is not None else {'error': '실행을 찾을 수 없습니다.'},
                                   200 if result is not None else 404, etag=result.get('version') if result else None)
                finally:
                    db.close()
                return
            if route.path == '/api/strategy/item' or (route.path == '/api/strategy' and params.get('view', [''])[0] in ('news', 'overview')):
                from strategy_views import read_view, read_item
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    if route.path == '/api/strategy/item':
                        result = read_item(db, params.get('id', [''])[0])
                    else:
                        result = read_view(db, params, source_service.status())
                    self.send_json(result if result is not None else {'error': '뉴스를 찾을 수 없습니다.'},
                                   200 if result is not None else 404)
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            baseline_match = re.fullmatch(r'/api/baseline/([a-zA-Z0-9_-]+)', route.path)
            if route.path == '/api/baseline' or baseline_match:
                if baseline_match:
                    result = baseline_service.get(baseline_match.group(1))
                    self.send_json(result or {'error': '기본 분석 실행을 찾을 수 없습니다.'}, 200 if result else 404)
                else:
                    self.send_json({'runs': baseline_service.list(), 'enabled': baseline_service.enabled})
                return
            if route.path == '/api/papers/pipeline':
                self.send_json(paper_pipeline.status());return
            if route.path == '/api/papers/strategy':
                from paper_context import strategic_context
                with sqlite3.connect(path,timeout=10) as db:self.send_json(strategic_context(db))
                return
            paper_match = re.fullmatch(r'/api/papers/(.+)', route.path)
            if route.path == '/api/papers' or paper_match:
                db = connect(path)
                try:
                    if paper_match:
                        item = paper(db, unquote(paper_match.group(1)))
                        result = {'paper': item, 'analysis': paper_analysis_service.get(item['paper_id'], item=item)} if item else {'error': '논문을 찾을 수 없습니다.'}
                        status = 200 if item else 404
                    else:
                        params = parse_qs(route.query)
                        if 'window_days' in params: params['window'] = params['window_days']
                        result = read_papers(db, params)
                        for item in result['items']:
                            item['analysis'] = paper_analysis_service.get(item['paper_id'], item=item)
                        result['metadata_service'] = paper_service.status()
                        result['pipeline'] = paper_pipeline.status()
                        result['analysis_service'] = paper_analysis_service.status()
                        from paper_graph import paper_coverage
                        result['analysis_coverage'] = paper_coverage(db)
                    self.send_json(result, status)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            improvement_match = re.fullmatch(r'/api/improvement/([a-zA-Z0-9_-]+)', route.path)
            if route.path == '/api/improvement' or improvement_match:
                if improvement_match:
                    result = public_improvement(improvement_service.get(improvement_match.group(1)))
                    if result:
                        result['catalog'] = improvement_catalog()
                    self.send_json(result or {'error': '자기개선 순환을 찾을 수 없습니다.'}, 200 if result else 404)
                else:
                    self.send_json({'runs': [public_improvement(run) for run in improvement_service.list()], 'enabled': workflow_service.enabled,
                                    'catalog': improvement_catalog()})
                return
            if route.path == '/api/risks/graph':
                from risk_graph import load_risk_graph
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    self.send_json(load_risk_graph(db, parse_qs(route.query)))
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            risk_match = re.fullmatch(r'/api/risks/([^/]+)', route.path)
            if risk_match or (route.path == '/api/risks' and params.get('view', [''])[0] == 'page'):
                from risk_views import read_risk_page, read_risk_detail
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    result = read_risk_detail(db, unquote(risk_match.group(1))) if risk_match else read_risk_page(db, params)
                    self.send_json(result if result else {'error':'위험 평가를 찾을 수 없습니다.'}, 200 if result else 404)
                except ValueError as error:
                    self.send_json({'error':str(error)},400)
                finally:
                    db.close()
                return
            if route.path == '/api/risks':
                from risk_analysis import read_risks
                db = connect(path)
                try:
                    self.send_json(read_risks(db, params=parse_qs(route.query)))
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            if route.path == '/api/sources':
                self.send_json(source_service.status())
                return
            if route.path == '/api/strategy':
                from strategy import read_strategy
                db = connect(path)
                try:
                    self.send_json(read_strategy(db, parse_qs(route.query), source_service.status()))
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            workflow_match = re.fullmatch(r'/api/workflows/([a-zA-Z0-9_-]+)', route.path)
            if route.path == '/api/workflows' or workflow_match:
                if workflow_match:
                    result = workflow_service.get_run(workflow_match.group(1))
                    self.send_json(result or {'error': '분석 사이클을 찾을 수 없습니다.'}, 200 if result else 404)
                else:
                    self.send_json({'enabled': workflow_service.enabled, 'runs': workflow_service.list_runs()})
                return
            simulation_match = re.fullmatch(r"/api/simulation/([a-zA-Z0-9_-]+)(/seed|/report)?", route.path)
            if route.path == "/api/simulation" or simulation_match:
                try:
                    if not simulation_match:
                        result = {"runtime": runtime_status(), "runs": simulation_service.list_runs()}
                    else:
                        run_id, resource = simulation_match.groups()
                        run = simulation_service.get_run(run_id)
                        if run is None:
                            result, status = {"error": "시뮬레이션을 찾을 수 없습니다."}, 404
                        elif resource == "/seed":
                            result = simulation_service.seed(run_id)
                        elif resource == "/report":
                            result = simulation_service.report(run_id)
                        else:
                            result = {"run": run}
                except (RuntimeError, ValueError, OSError):
                    result, status = {"error": "시뮬레이션 기록을 불러오지 못했습니다."}, 400
                self.send_json(result, status)
                return
            research_match = re.fullmatch(r"/api/research/([a-zA-Z0-9_-]+)(/documents)?", route.path)
            if route.path == "/api/research" or research_match:
                try:
                    if not research_match:
                        result = {"enabled": research_service.enabled, "runs": research_service.list_runs()}
                    else:
                        run_id = research_match.group(1)
                        result = research_service.get_run(run_id)
                        if result is None:
                            result, status = {"error": "분석 스냅샷을 찾을 수 없습니다."}, 404
                        elif research_match.group(2):
                            params = parse_qs(route.query)
                            result = research_service.documents(run_id, page=int(params.get("page", ["1"])[0]),
                                                                page_size=min(100, int(params.get("page_size", ["24"])[0])))
                except ValueError:
                    result, status = {"error": "분석 범위 또는 페이지를 확인해 주세요."}, 400
                self.send_json(result, status)
                return
            if route.path == "/api/graph/integrated":
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    result = load_integrated_graph(db, parse_qs(route.query))
                except ValueError:
                    result, status = {"error": "그래프 필터를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path in {"/api/urls", "/api/urls/export.json"}:
                db = connect(path)
                try:
                    params = parse_qs(route.query)
                    result = archived_rows(db, params)
                    if route.path.endswith("export.json"):
                        params.update(page=["1"], page_size=["100"])
                        result = archived_rows(db, params)
                        items = list(result["items"])
                        for page in range(2, result["total_pages"] + 1):
                            params["page"] = [str(page)]
                            items.extend(archived_rows(db, params)["items"])
                        result = {"items": items, "total": len(items)}
                except ValueError:
                    result, status = {"error": "보관함 날짜 또는 페이지를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/keywords/document":
                db = connect(path)
                try:
                    params = parse_qs(route.query)
                    record_id = params.get("id", [""])[0]
                    result = read_keyword_record(db, record_id)
                    if result is None:
                        result, status = {"error": "키워드 기록을 찾을 수 없습니다."}, 404
                except ValueError:
                    result, status = {"error": "키워드 기록 ID를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/briefing":
                db = connect(path)
                try:
                    result = read_briefing(db, parse_qs(route.query))
                except ValueError:
                    result, status = {"error": "브리핑 날짜를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/graph":
                db = connect(path)
                try:
                    selected = graph_input(db, parse_qs(route.query))
                    result = {"input": selected, "analysis": graph_service.status(selected)}
                except ValueError:
                    result, status = {"error": "분석 범위와 날짜를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/links" or re.fullmatch(r"/api/links/[0-9a-f]{24}", route.path):
                db = connect(path)
                try:
                    if route.path == "/api/links":
                        result = read_links(db, parse_qs(route.query), analysis_service)
                    else:
                        group = find_link_group(db, route.path.rsplit("/", 1)[1])
                        if group is None:
                            result, status = {"error": "링크 그룹을 찾을 수 없습니다."}, 404
                        else:
                            result = dict(group, comparison=compare_mentions(group), analysis=analysis_service.status(group))
                except ValueError:
                    result, status = {"error": "날짜 또는 페이지 번호를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/news":
                db = connect(path)
                try:
                    result = read_news(db, parse_qs(route.query))
                except ValueError:
                    status, result = 400, {"error": "올바른 날짜를 선택해 주세요."}
                finally:
                    db.close()
                content = json.dumps(result, ensure_ascii=False).encode()
                mime = "application/json; charset=utf-8"
            elif route.path in {'/observatory-search.js', '/observatory.js', '/observatory.css', '/strategy.js', '/strategy.css', '/news-network.js', '/news-network.css', '/papers.js', '/papers.css', '/risks.js', '/risks.css', '/risk-network.js', '/risk-network.css', '/intelligence.js', '/intelligence.css'}:
                content = (ROOT / 'static' / route.path[1:]).read_bytes()
                if route.path == '/risks.js':
                    content += b'\n' + (ROOT / 'static/risk-network-bootstrap.js').read_bytes()
                mime = 'application/javascript; charset=utf-8' if route.path.endswith('.js') else 'text/css; charset=utf-8'
            elif route.path in {"/graph", "/graph.js", "/archive", "/research", "/research.js", "/simulation", "/simulation.js"}:
                name = {"/graph": "graph.html", "/graph.js": "graph.js", "/archive": "archive.html", "/research": "research.html", "/research.js": "research.js", "/simulation": "simulation.html", "/simulation.js": "simulation.js"}[route.path]
                content = (ROOT / "static" / name).read_bytes()
                mime = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/html; charset=utf-8"
            elif route.path in {'/intelligence','/intelligence/events','/intelligence/topics','/intelligence/decisions',
                                '/intelligence/scenarios','/intelligence/research','/intelligence/operations',
                                '/intelligence/concepts','/intelligence/experiments','/intelligence/profiles','/intelligence/risk_history'}:
                content = (ROOT / 'static/intelligence.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == '/observatory':
                content = (ROOT / 'static/observatory.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == '/risks':
                content = (ROOT / 'static/risks.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == '/papers':
                content = (ROOT / 'static/papers.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == "/mirofish-license":
                content = (ROOT / "vendor/mirofish/LICENSE").read_bytes()
                mime = "text/plain; charset=utf-8"
            elif route.path == "/mirofish-source.zip":
                content, mime = source_bundle(), "application/zip"
            elif route.path == '/news' or (route.path == '/' and route.query):
                content = (ROOT / "static/index.html").read_bytes()
                mime = "text/html; charset=utf-8"
            elif route.path in {'/', '/strategy'}:
                content = (ROOT / 'static/strategy.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            else:
                status, content, mime = 404, b"Not found", "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            route = urlparse(self.path)
            intelligence_action = re.fullmatch(r'/api/intelligence/(events|concepts|decisions|scenarios|profiles|experiments|research|query)',route.path)
            match = re.fullmatch(r"/api/links/([0-9a-f]{24})/analyze", route.path)
            is_graph = route.path == "/api/graph/analyze"
            is_question = route.path == "/api/graph/ask"
            is_research = route.path == "/api/research"
            is_simulation = route.path == "/api/simulation"
            is_reach = route.path in ('/api/wiki','/api/article-explanations','/api/agent-reach/read','/api/agent-reach/repair','/api/agent-reach/subscriptions','/api/agent-reach/subscription-toggle')
            is_source = route.path == '/api/sources'
            is_workflow = route.path == '/api/workflows'
            is_improvement = route.path == '/api/improvement'
            is_topic = route.path == '/api/strategy/topics'
            topic_action = re.fullmatch(r'/api/strategy/topics/([^/]+)/(exclude|restore)', route.path)
            is_baseline = route.path == '/api/baseline'
            baseline_action = re.fullmatch(r'/api/baseline/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
            is_paper_refresh = route.path in ('/api/papers/refresh','/api/papers/pipeline')
            is_paper_analysis = route.path == '/api/papers/analyze'
            paper_action = re.fullmatch(r'/api/papers/(.+)/analyze', route.path)
            improvement_action = re.fullmatch(r'/api/improvement/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
            workflow_resume = re.fullmatch(r'/api/workflows/([a-zA-Z0-9_-]+)/resume', route.path)
            is_runtime_start = route.path == "/api/simulation/runtime/start"
            simulation_action = re.fullmatch(r"/api/simulation/([a-zA-Z0-9_-]+)/(start|stop|resume|interview|chat)", route.path)
            resume_match = re.fullmatch(r"/api/research/([a-zA-Z0-9_-]+)/resume", route.path)
            if not is_reach and not intelligence_action and not match and not is_graph and not is_question and not is_research and not resume_match and not is_simulation and not simulation_action and not is_runtime_start and not is_source and not is_workflow and not workflow_resume and not is_improvement and not improvement_action and not is_paper_refresh and not is_paper_analysis and not paper_action and not is_baseline and not baseline_action and not is_topic and not topic_action:
                self.send_json({"error": "Not found"}, 404)
                return
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if (host not in {f"127.0.0.1:{port}", f"localhost:{port}"}
                    or (origin and origin != f"http://{host}")
                    or self.headers.get_content_type() != "application/json"):
                self.send_json({"error": "뉴스 페이지에서 분석을 요청해 주세요."}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > (32768 if is_reach or intelligence_action or is_topic or topic_action or is_question or is_simulation or simulation_action or is_workflow or is_improvement or is_paper_refresh or is_paper_analysis or paper_action else 1024):
                    raise ValueError
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "분석 요청 형식이 올바르지 않습니다."}, 400)
                return
            if route.path == '/api/wiki':
                try:
                    action = payload.get('action', 'refresh')
                    if action == 'configure':result = wiki_service.manage(payload)
                    elif action == 'alias':result = wiki_service.alias(payload)
                    elif action == 'refresh':result = wiki_service.request(payload.get('topic', ''))
                    elif action == 'archive_question':
                        job_id=payload.get('job_id')
                        if not isinstance(job_id,str):raise ValueError('질문 작업 ID가 필요합니다.')
                        job=question_service.get(job_id)
                        if not job or job['status']!='complete':raise ValueError('현재 검토 완료된 질문만 저장할 수 있습니다.')
                        with question_service.db() as db:
                            row=db.execute('SELECT request_json FROM graph_question_jobs WHERE id=?',(job_id,)).fetchone()
                        result=wiki_service.archive_question(job_id,json.loads(row[0])['question'],job['result'])
                    else:raise ValueError('지원하지 않는 위키 작업입니다.')
                    self.send_json(result, 202)
                except ValueError as error:self.send_json({'error': str(error)}, 400)
                except sqlite3.OperationalError:self.send_json({'error': '저장소가 갱신 중입니다. 다시 요청해 주세요.'}, 503)
                return
            if route.path == '/api/article-explanations':
                try:self.send_json(article_service.submit(payload.get('url','')),202)
                except ValueError as error:self.send_json({'error':str(error)},400)
                except RuntimeError as error:self.send_json({'error':str(error)},429)
                return
            if intelligence_action:
                try:
                    result=intelligence_service.mutate(intelligence_action.group(1),payload)
                    self.send_json(result,202 if result.get('run') else 200)
                except (ValueError,TypeError) as error:
                    self.send_json({'error':str(error)},400)
                except RuntimeError as error:
                    self.send_json({'error':str(error)},409)
                return
            if is_topic or topic_action:
                from dynamic_registry import save_manual, set_excluded
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    if topic_action:
                        topic_id, action = topic_action.groups()
                        result = set_excluded(db, unquote(topic_id), action == 'exclude')
                    else:
                        result = save_manual(db, payload)
                    self.send_json({'item': result} if result else {'error': '주제를 찾을 수 없습니다.'},
                                   200 if result else 404)
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            if is_baseline or baseline_action:
                try:
                    if baseline_action:
                        run_id, action = baseline_action.groups()
                        result = getattr(baseline_service, action)(run_id)
                    else:
                        result = baseline_service.start(payload)
                    self.send_json({'run': result}, 202)
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                except RuntimeError as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_paper_refresh or is_paper_analysis or paper_action:
                try:
                    if route.path == '/api/papers/pipeline':
                        self.send_json(paper_pipeline.configure(payload.get('enabled')),200);return
                    ids = [unquote(paper_action.group(1))] if paper_action else payload.get('ids')
                    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                        raise ValueError('논문 ID 목록 형식이 올바르지 않습니다.')
                    params = parse_qs(route.query)
                    if ids is None and any(params.get(k) for k in ('q','category','sector','analysis_status')):
                        db = connect(path)
                        try:
                            ids = [item['paper_id'] for item in read_papers(db, dict(params, page=['1'],page_size=['50']))['items']]
                        finally:
                            db.close()
                    if is_paper_refresh:
                        result = paper_service.refresh(ids, limit=int(payload.get('limit',20)))
                    else:
                        result = paper_analysis_service.submit(ids[:10] if ids is not None else None, limit=1 if paper_action else int(payload.get('limit',3)))
                    self.send_json(result, 202)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_improvement or improvement_action:
                try:
                    if improvement_action:
                        cycle_id, action = improvement_action.groups()
                        result = getattr(improvement_service, action)(cycle_id)
                    else:
                        settings = dict(payload)
                        scope = {key: values[0] for key, values in parse_qs(route.query).items()}
                        allowed_scope = {'date', 'topic', 'q', 'channel', 'keyword', 'content_type', 'lens', 'terms', 'strategic_keyword', 'impact', 'sort', 'sector'}
                        if set(scope)-allowed_scope:
                            raise ValueError('자기개선 뉴스 범위를 확인해 주세요.')
                        settings['scope'] = scope or settings.get('scope', {})
                        if not isinstance(settings['scope'], dict) or set(settings['scope'])-allowed_scope:
                            raise ValueError('자기개선 뉴스 범위 형식이 올바르지 않습니다.')
                        if any(not isinstance(v, str) or len(v)>2000 for v in settings['scope'].values()):
                            raise ValueError('자기개선 필터 값이 올바르지 않습니다.')
                        if 'full_corpus' in settings and not isinstance(settings['full_corpus'], bool):
                            raise ValueError('전체 뉴스 선택 값이 올바르지 않습니다.')
                        settings.setdefault('full_corpus', True)
                        settings.setdefault('max_rounds', 0)
                        settings.setdefault('interval_seconds', 5)
                        result = improvement_service.start(settings)
                    self.send_json({'run': public_improvement(result)}, 202)
                except (ValueError, RuntimeError, TypeError) as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_workflow or workflow_resume:
                try:
                    if workflow_resume:
                        result = workflow_service.resume(workflow_resume.group(1))
                    else:
                        from strategy import select_strategy_items
                        params = parse_qs(route.query)
                        limit = int(payload.get('limit', 8))
                        if not 1 <= limit <= 24:
                            raise ValueError('분석 사이클은 1~24개 뉴스를 선택해 주세요.')
                        db = connect(path)
                        try:
                            selected = select_strategy_items(db, params)[:limit]
                        finally:
                            db.close()
                        result = workflow_service.create_run(selected, dict(payload, scope={k:v[0] for k,v in params.items()}))
                    self.send_json({'run': result}, 202)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_reach:
                try:
                    if route.path.endswith('/repair'):result=reach_pipeline.repair(payload.get('limit',50))
                    elif route.path.endswith('/subscriptions'):result=reach_pipeline.add_subscription(payload)
                    elif route.path.endswith('/subscription-toggle'):result=reach_pipeline.toggle(payload.get('id'),payload.get('enabled'))
                    else:result=reach_service.submit(payload.get('url'),payload.get('mode','auto'))
                    self.send_json(result,202)
                except ValueError as error:self.send_json({'error':str(error)},400)
                return
            if is_source:
                try:
                    db = connect(path)
                    try:
                        from strategy import select_strategy_items
                        params = parse_qs(route.query)
                        selected = select_strategy_items(db, params)
                        requested = payload.get('url')
                        urls = list(dict.fromkeys(item['source_url'] for item in selected if item.get('source_url')))
                        if requested:
                            if requested not in urls:
                                raise ValueError('현재 뉴스 범위에 포함된 URL을 선택해 주세요.')
                            urls = [requested]
                        limit = max(1, min(100, int(payload.get('limit', 40))))
                    finally:
                        db.close()
                    self.send_json(dict(source_service.submit(urls[:limit]), available=len(urls)), 202)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 400)
                return
            if is_runtime_start:
                try:
                    from mirofish_runtime import start_runtime
                    start_runtime()
                    self.send_json({"runtime": runtime_status()})
                except (RuntimeError, ValueError, OSError):
                    self.send_json({"error": "엔진을 시작하지 못했습니다. 설치 상태와 .env.mirofish 설정을 확인해 주세요.", "runtime": runtime_status()}, 409)
                return
            if is_simulation or simulation_action:
                try:
                    if is_simulation:
                        db = connect(path)
                        try:
                            items = simulation_news(db, payload, prepared=True)
                        finally:
                            db.close()
                        result = simulation_service.create_run(items, payload)
                    else:
                        run_id, action = simulation_action.groups()
                        if simulation_service.get_run(run_id) is None:
                            self.send_json({"error": "시뮬레이션을 찾을 수 없습니다."}, 404)
                            return
                        if action == "interview":
                            result = simulation_service.interview(run_id, payload.get("agent_id"), payload.get("prompt", ""), payload.get("platform"))
                            self.send_json({"result": result}); return
                        if action == "chat":
                            result = simulation_service.report_chat(run_id, payload.get("message", ""), payload.get("chat_history", []))
                            self.send_json({"result": result}); return
                        result = getattr(simulation_service, action)(run_id)
                    self.send_json({"run": result}, 202)
                except ValueError as error:
                    self.send_json({"error": str(error)}, 400)
                except RuntimeError as error:
                    self.send_json({"error": str(error)}, 409)
                return
            if is_research or resume_match:
                if not research_service.enabled:
                    self.send_json({"error": "외부 분석이 비활성화되어 있습니다."}, 403)
                    return
                try:
                    if resume_match:
                        result = research_service.resume(resume_match.group(1))
                    else:
                        db = connect(path)
                        try:
                            db.execute("BEGIN")
                            rows = [dict(row) for row in joined_articles(db)]
                            rows.extend(unindexed_link_rows(db, rows))
                            rows.extend(hidden_link_rows(db, rows))
                            records = archived_rows(db, {"active": ["1"], "page_size": ["100"]})
                            archive = list(records["items"])
                            for page in range(2, records["total_pages"] + 1):
                                archive.extend(archived_rows(db, {"active": ["1"], "page_size": ["100"], "page": [str(page)]})["items"])
                        finally:
                            db.close()
                        if payload.get('sample_limit') is not None:
                            limit = int(payload['sample_limit'])
                            if not 1 <= limit <= 100:
                                raise ValueError('선택 분석은 1~100개 뉴스로 제한됩니다.')
                            db = connect(path)
                            try:
                                from strategy import select_strategy_items
                                params = parse_qs(route.query)
                                rows = select_strategy_items(db, params)[:limit]
                            finally:
                                db.close()
                            archive = []
                        result = research_service.create_run(rows, archive)
                    self.send_json({"run": result} if result else {"error": "분석 스냅샷을 찾을 수 없습니다."}, 202 if result else 404)
                except (RuntimeError, ValueError) as error:
                    self.send_json({"error": str(error)}, 409)
                return
            if is_question:
                question = payload.get("question", "")
                ids = payload.get("node_ids", [])
                if not isinstance(question, str) or len(question.strip()) < 3 or len(question) > 2000 or not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(i, str) for i in ids):
                    self.send_json({"error": "질문과 선택 키워드를 확인해 주세요."}, 400)
                    return
                try:
                    answer=question_service.ask(question,ids,parse_qs(route.query))
                    self.send_json(answer)
                except (RuntimeError, ValueError) as error:
                    self.send_json({"error": str(error)}, 422)
                except sqlite3.OperationalError:
                    self.send_json({'error':'근거 저장소가 갱신 중입니다. 잠시 후 다시 질문해 주세요.'},503)
                return
            db = connect(path)
            try:
                group = graph_input(db, parse_qs(route.query)) if is_graph else find_link_group(db, match.group(1))
            except ValueError:
                self.send_json({"error": "분석 범위와 날짜를 확인해 주세요."}, 400)
                return
            finally:
                db.close()
            if group is None:
                self.send_json({"error": "링크 그룹을 찾을 수 없습니다."}, 404)
                return
            service = graph_service if is_graph else analysis_service
            if not group.get("mentions"):
                self.send_json({"error": "분석할 메시지가 없습니다."}, 400)
                return
            if not service.enabled:
                self.send_json({"error": "외부 의미 분석이 아직 활성화되지 않았습니다."}, 403)
                return
            try:
                status = service.submit(group)
                self.send_json({"status": status}, 200 if status == "complete" else 202)
            except RuntimeError as error:
                self.send_json({"error": str(error)}, 429)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    warm_stop = threading.Event()
    warm_thread = threading.Thread(target=warm_strategy_views, args=(path, warm_stop, source_service.status),
                                   daemon=True, name='news-view-warmup')
    warm_thread.start()
    from automation_runtime import recover
    recover(recovery_candidates, {'baseline':baseline_service, 'improvement':improvement_service, 'workflows':workflow_service})
    print(f"뉴스 페이지: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        warm_stop.set()
        wiki_service.close()
        article_service.close()
        question_service.close()
        observatory_service.close()
        reach_service.close()
        reach_pipeline.close()
        corpus_status.close()
        server.server_close()
        warm_thread.join(timeout=2)
        intelligence_service.close()
        analysis_service.close()
        graph_service.close()
        research_service.close()
        baseline_service.close()
        improvement_service.close()
        workflow_service.close()
        paper_pipeline.close()
        paper_analysis_service.close()
        paper_service.close()
        source_service.close()
        simulation_service.close()


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
