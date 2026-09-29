"""News read projections and stable article/message deduplication."""
from task_lifecycle import checkpoint
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from classification import TOPICS
from link_groups import canonical_url, classify_link, extract_links
from url_archive import archived_link_rows
from source_enrichment import attach_sources
from keyword_index import annotate_items, keyword_discovery, public_item
KST = ZoneInfo("Asia/Seoul")
CONTENT_TYPES = {"news": "뉴스 기사", "paper": "논문 소개", "blog": "블로그·해설", "social": "소셜 게시물", "tool": "도구·프로젝트", "other": "기타"}

def normalized_message(text):
    """Ignore cosmetic spacing, preserving case, punctuation and content dates."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def duplicate_message_keys(db):
    """Keep the first copy per channel, regardless of Telegram posting date."""
    seen, duplicates = set(), set()
    hidden = {}
    for url in db.execute("SELECT chat_id,message_id,original_url,title FROM archived_urls WHERE active=1 AND entity_type='text_link'"):
        checkpoint()
        hidden.setdefault((url["chat_id"], url["message_id"]), set()).add((url["original_url"], url["title"]))
    for row in db.execute("SELECT chat_id,message_id,text FROM news ORDER BY published_at,message_id,chat_id"):
        checkpoint()
        key = (row["chat_id"], normalized_message(row["text"]),
               tuple(sorted(hidden.get((row["chat_id"], row["message_id"]), set()))))
        if key in seen:
            duplicates.add((row["chat_id"], row["message_id"]))
        else:
            seen.add(key)
    return duplicates


def article_key(item):
    """Merge identical same-day descriptions, never URL-only updates."""
    if item.get("_repository_key"):
        return item["_repository_key"]
    text = item["text"]
    for url in sorted(extract_links(text), key=len, reverse=True):
        checkpoint()
        text = text.replace(url, canonical_url(url) or url)
    return hashlib.sha256(json.dumps([item["day"], normalized_message(text), item.get("embedded_urls", [])],ensure_ascii=False).encode()).hexdigest()


def unique_articles(rows):
    grouped = {}
    for row in rows:
        checkpoint()
        item = dict(row)
        key = article_key(item)
        item.pop("_repository_key", None)
        source = {name: item[name] for name in ("chat_id", "message_id", "channel", "url")}
        if key not in grouped:
            item.update(classify_link(item["source_url"], item["title"], item["text"]))
            item["topic_title"] = TOPICS.get(item["topic"], TOPICS["general"])
            item["sources"] = [source]
            grouped[key] = item
        elif source not in grouped[key]["sources"]:
            grouped[key]["sources"].append(source)
    for item in grouped.values():
        checkpoint()
        item["source_count"] = len(item["sources"])
    return list(grouped.values())


def joined_articles(db, *, _message=None, _duplicates=None):
    if _message is None:
        from source_projection import joined
        prepared=joined(db)
        if prepared is not None:return prepared
    duplicates = duplicate_message_keys(db) if _duplicates is None else _duplicates
    rows = db.execute("""SELECT a.*, n.channel, n.url, n.published_at,
        n.day AS telegram_day FROM articles a JOIN news n
        ON a.chat_id=n.chat_id AND a.message_id=n.message_id
        """ + (' WHERE a.chat_id=? AND a.message_id=?' if _message is not None else '') +
        ' ORDER BY a.day DESC, n.published_at DESC, a.message_id DESC, a.item_index', _message or ()).fetchall()
    hidden = {}
    for url in db.execute("SELECT chat_id,message_id,original_url FROM archived_urls WHERE active=1 AND entity_type='text_link'" + (" AND chat_id=? AND message_id=?" if _message is not None else ""), _message or ()):
        checkpoint()
        hidden.setdefault((url["chat_id"], url["message_id"]), set()).add(url["original_url"])
    from reach_pipeline import external_rows
    return attach_sources(db, [dict(row, embedded_urls=sorted(hidden.get((row["chat_id"], row["message_id"]), set())))
            for row in rows if (row["chat_id"], row["message_id"]) not in duplicates]+(external_rows(db) if _message is None else []))


def read_news(db, params, *, include_discovery=True):
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
    discovery = keyword_discovery(db, items, day, keyword) if include_discovery else None
    items = [public_item(item) for item in items]
    return {"date": day, "dates": dates, "channels": channels,
            "topics": topics, "types": [{"id": key, "title": title, "count": type_counts[key]}
                                           for key, title in CONTENT_TYPES.items()],
            "items": items,
            "keyword_discovery": discovery,
            "raw_message_count": db.execute("SELECT COUNT(*) FROM news").fetchone()[0],
            "demo": db.execute("SELECT 1 FROM state WHERE key='demo'").fetchone() is not None}


def unindexed_link_rows(db, indexed_rows, *, _message=None, _duplicates=None):
    """Keep source-only fragments that Telegram message splitting left outside articles."""
    represented = {}
    for row in indexed_rows:
        checkpoint()
        key = (str(row["chat_id"]), row["message_id"])
        represented.setdefault(key, set()).update(canonical_url(url) for url in
                                                 extract_links(row["text"]) + [row["source_url"]] if url)
    extras = []
    duplicates = duplicate_message_keys(db) if _duplicates is None else _duplicates
    for raw in db.execute("SELECT * FROM news" + (" WHERE chat_id=? AND message_id=?" if _message is not None else ""), _message or ()):
        checkpoint()
        key = (str(raw["chat_id"]), raw["message_id"])
        if key in duplicates:
            continue
        known = represented.setdefault(key, set())
        for url in extract_links(raw["text"]):
            checkpoint()
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


def hidden_link_rows(db, existing_rows, *, _message=None, _duplicates=None):
    duplicates = duplicate_message_keys(db) if _duplicates is None else _duplicates
    known = {}
    for row in existing_rows:
        checkpoint()
        key = (str(row["chat_id"]), row["message_id"])
        known.setdefault(key, set()).update(canonical_url(url) for url in
            extract_links(row["text"]) + [row["source_url"]] if url)
    result = []
    for row in archived_link_rows(db, hidden_only=True, message=_message):
        checkpoint()
        key = (str(row["chat_id"]), row["message_id"])
        url = canonical_url(row["source_url"])
        if key in duplicates or url in known.get(key, set()):
            continue
        known.setdefault(key, set()).add(url)
        result.append(row)
    return result
