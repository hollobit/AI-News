"""Lossless local archive of URLs observed in Telegram channel messages."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from link_groups import canonical_url


_URL_START = re.compile(r"https?://", re.IGNORECASE)
_MARKDOWN_START = re.compile(r"\[([^\]\r\n]+)\]\((?=https?://)", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?\"'…，。；：！？"
KST = ZoneInfo("Asia/Seoul")


def init_archive(db):
    """Create archive tables without changing the existing news schema."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS message_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            day TEXT NOT NULL,
            channel TEXT NOT NULL,
            telegram_url TEXT NOT NULL DEFAULT '',
            body_kind TEXT NOT NULL,
            body TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_origin TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            archived_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS message_snapshots_message
            ON message_snapshots(chat_id, message_id, active, version);
        CREATE TABLE IF NOT EXISTS archived_urls (
            snapshot_id TEXT NOT NULL,
            occurrence INTEGER NOT NULL,
            chat_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            day TEXT NOT NULL,
            published_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            telegram_url TEXT NOT NULL DEFAULT '',
            original_url TEXT NOT NULL,
            canonical_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            title_source TEXT NOT NULL,
            verified_article_title INTEGER NOT NULL DEFAULT 0,
            origin TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            nearby_context TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL CHECK(active IN (0, 1)),
            PRIMARY KEY(snapshot_id, occurrence),
            FOREIGN KEY(snapshot_id) REFERENCES message_snapshots(snapshot_id)
        );
        CREATE INDEX IF NOT EXISTS archived_urls_lookup
            ON archived_urls(active, day, chat_id, message_id);
        CREATE INDEX IF NOT EXISTS archived_urls_canonical
            ON archived_urls(canonical_url);
        CREATE TABLE IF NOT EXISTS url_archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)


def _clean_url_tail(candidate):
    value = candidate.rstrip(_TRAILING_PUNCTUATION)
    while value and value[-1] in ")]}":
        closer = value[-1]
        opener = {")": "(", "]": "[", "}": "{"}[closer]
        if value.count(closer) > value.count(opener):
            value = value[:-1].rstrip(_TRAILING_PUNCTUATION)
        else:
            break
    return value


def _valid_http_url(value):
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.hostname)
    except (AttributeError, ValueError):
        return False


def _utf16_slice(text, offset, length):
    """Apply Telegram's UTF-16 code-unit offsets, including astral emoji."""
    try:
        encoded = text.encode("utf-16-le")
        start, end = int(offset) * 2, (int(offset) + int(length)) * 2
        if start < 0 or end < start or end > len(encoded):
            return None
        return encoded[start:end].decode("utf-16-le")
    except (UnicodeError, TypeError, ValueError):
        return None


def _utf16_to_codepoint_span(text, offset, length):
    try:
        encoded = text.encode("utf-16-le")
        start_bytes, end_bytes = int(offset) * 2, (int(offset) + int(length)) * 2
        if start_bytes < 0 or end_bytes < start_bytes or end_bytes > len(encoded):
            return None
        start = len(encoded[:start_bytes].decode("utf-16-le"))
        end = start + len(encoded[start_bytes:end_bytes].decode("utf-16-le"))
        return start, end
    except (UnicodeError, TypeError, ValueError):
        return None


def _context(text, start, end, limit=240):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    current = re.sub(r"\s+", " ", text[line_start:line_end]).strip()
    if current and current != text[start:end].strip():
        return current[:limit]
    previous_end = max(0, line_start - 1)
    previous_start = text.rfind("\n", 0, previous_end) + 1
    previous = re.sub(r"\s+", " ", text[previous_start:previous_end]).strip()
    return (previous or current)[:limit]


def _inferred_title(context, url):
    value = context.replace(url, " ")
    value = re.sub(r"^[\s\-–—*•#>]+|[\s\-–—:|]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()[:200]


def _entity_occurrences(text, entities, body_kind):
    found = []
    occupied = []
    for entity_index, entity in enumerate(entities or []):
        entity_type = str(entity.get("type") or "")
        if entity_type not in {"url", "text_link"}:
            continue
        span = _utf16_to_codepoint_span(text, entity.get("offset"), entity.get("length"))
        anchor = _utf16_slice(text, entity.get("offset"), entity.get("length"))
        if span is None or anchor is None:
            continue
        original_url = (anchor if entity_type == "url" else
                        str(entity.get("url") or "")).strip()
        if not _valid_http_url(original_url):
            continue
        start, end = span
        occupied.append((start, end, original_url))
        nearby = _context(text, start, end)
        title = anchor.strip() if entity_type == "text_link" else _inferred_title(nearby, anchor)
        found.append({
            "sort": (start, 0, entity_index), "original_url": original_url,
            "title": title,
            "title_source": "telegram_anchor" if entity_type == "text_link" else "nearby_context",
            "verified_article_title": 0, "origin": f"{body_kind}_entity",
            "entity_type": entity_type, "nearby_context": nearby,
        })
    return found, occupied


def _markdown_occurrences(text):
    """Yield Markdown links while retaining balanced parentheses inside URLs."""
    for markdown_index, match in enumerate(_MARKDOWN_START.finditer(text)):
        url_start = match.end()
        cursor, depth = url_start, 1
        while cursor < len(text):
            char = text[cursor]
            if char.isspace() or char in "<>\"`":
                break
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    raw = text[url_start:cursor]
                    if _valid_http_url(raw):
                        yield markdown_index, match, url_start, cursor, raw
                    break
            cursor += 1


def _text_occurrences(text, entities, body_kind):
    found, occupied = _entity_occurrences(text, entities, body_kind)
    markdown_spans = []
    for markdown_index, match, url_start, url_end, raw in _markdown_occurrences(text):
        if any(start <= url_start < end for start, end, _ in occupied):
            continue
        markdown_spans.append((url_start, url_end))
        found.append({
            "sort": (url_start, 1, markdown_index), "original_url": raw,
            "title": match.group(1).strip(), "title_source": "markdown_anchor",
            "verified_article_title": 0, "origin": f"{body_kind}_markdown",
            "entity_type": "markdown_link", "nearby_context": _context(text, match.start(), url_end + 1),
        })
    plain_index = 0
    for match in _URL_START.finditer(text):
        end = match.end()
        while end < len(text) and not text[end].isspace() and text[end] not in '<>"`':
            end += 1
        if any(start <= match.start() < stop for start, stop, _ in occupied):
            continue
        if any(start <= match.start() < stop for start, stop in markdown_spans):
            continue
        raw = _clean_url_tail(text[match.start():end])
        if not _valid_http_url(raw):
            continue
        nearby = _context(text, match.start(), end)
        found.append({
            "sort": (match.start(), 2, plain_index), "original_url": raw,
            "title": _inferred_title(nearby, raw), "title_source": "nearby_context",
            "verified_article_title": 0, "origin": f"{body_kind}_plain",
            "entity_type": "", "nearby_context": nearby,
        })
        plain_index += 1
    return sorted(found, key=lambda item: item.pop("sort"))


def extract_message_urls(message):
    """Return every URL occurrence, retaining same-URL repetitions."""
    occurrences = []
    for body_kind, entity_key in (("text", "entities"), ("caption", "caption_entities")):
        body = str(message.get(body_kind) or "")
        if body:
            occurrences.extend(_text_occurrences(body, message.get(entity_key), body_kind))
    return occurrences


def _telegram_url(message, chat_id):
    supplied = str(message.get("_archive_telegram_url") or "")
    if supplied:
        return supplied
    username = str((message.get("chat") or {}).get("username") or "")
    if username and re.fullmatch(r"[A-Za-z0-9_]+", username):
        return f"https://t.me/{username}/{int(message['message_id'])}"
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{int(message['message_id'])}"
    return ""


def archive_message(db, message):
    """Archive one already-accepted Telegram revision inside the caller transaction."""
    chat = message["chat"]
    chat_id, message_id = str(chat["id"]), int(message["message_id"])
    version = int(message.get("edit_date", message.get("date", 0)))
    payload = {key: value for key, value in message.items() if not str(key).startswith("_archive_")}
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest_source = f"{chat_id}\0{message_id}\0{version}\0{payload_json}"
    snapshot_id = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    existing = db.execute(
        "SELECT active FROM message_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if existing:
        if not existing["active"]:
            db.execute(
                "UPDATE message_snapshots SET active=0 WHERE chat_id=? AND message_id=? AND active=1",
                (chat_id, message_id))
            db.execute(
                "UPDATE archived_urls SET active=0 WHERE chat_id=? AND message_id=? AND active=1",
                (chat_id, message_id))
            db.execute("UPDATE message_snapshots SET active=1 WHERE snapshot_id=?", (snapshot_id,))
            db.execute("UPDATE archived_urls SET active=1 WHERE snapshot_id=?", (snapshot_id,))
        return snapshot_id

    timestamp = int(message.get("date", version or 0))
    published_at = str(message.get("_archive_published_at") or
                       datetime.fromtimestamp(timestamp, KST).isoformat())
    day = str(message.get("_archive_day") or published_at[:10])
    text, caption = str(message.get("text") or ""), str(message.get("caption") or "")
    body_kind = "text" if text else ("caption" if caption else "empty")
    body = text if text else caption
    channel = str(chat.get("title") or chat_id)
    telegram_url = _telegram_url(message, chat_id)
    archived_at = datetime.now(timezone.utc).isoformat()

    db.execute("UPDATE message_snapshots SET active=0 WHERE chat_id=? AND message_id=? AND active=1",
               (chat_id, message_id))
    db.execute("UPDATE archived_urls SET active=0 WHERE chat_id=? AND message_id=? AND active=1",
               (chat_id, message_id))
    db.execute("""INSERT INTO message_snapshots
        (snapshot_id,chat_id,message_id,version,published_at,day,channel,telegram_url,
         body_kind,body,payload_json,payload_origin,active,archived_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (snapshot_id, chat_id, message_id, version, published_at, day, channel, telegram_url,
         body_kind, body, payload_json, str(message.get("_archive_source") or "telegram"), archived_at))
    _store_url_occurrences(db, {
        "snapshot_id": snapshot_id, "chat_id": chat_id, "message_id": message_id,
        "version": version, "day": day, "published_at": published_at, "channel": channel,
        "telegram_url": telegram_url, "active": 1,
    }, message)
    return snapshot_id


def _store_url_occurrences(db, snapshot, message):
    occurrences = extract_message_urls(message)
    for occurrence, item in enumerate(occurrences):
        db.execute("""INSERT INTO archived_urls
            (snapshot_id,occurrence,chat_id,message_id,version,day,published_at,channel,
             telegram_url,original_url,canonical_url,title,title_source,verified_article_title,
             origin,entity_type,nearby_context,active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snapshot["snapshot_id"], occurrence, snapshot["chat_id"], snapshot["message_id"],
             snapshot["version"], snapshot["day"], snapshot["published_at"], snapshot["channel"],
             snapshot["telegram_url"], item["original_url"], canonical_url(item["original_url"]), item["title"],
             item["title_source"], item["verified_article_title"], item["origin"],
             item["entity_type"], item["nearby_context"], snapshot["active"]))
    return len(occurrences)


def reindex_archive_urls(db):
    """Rebuild URL occurrences from stored payloads without altering snapshots."""
    snapshots = db.execute("""SELECT snapshot_id,chat_id,message_id,version,day,published_at,
        channel,telegram_url,active,payload_json FROM message_snapshots
        ORDER BY published_at,message_id,snapshot_id""").fetchall()
    prepared = []
    for snapshot in snapshots:
        try:
            message = json.loads(snapshot["payload_json"])
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError(f"손상된 메시지 스냅샷: {snapshot['snapshot_id']}") from error
        if not isinstance(message, dict):
            raise ValueError(f"잘못된 메시지 스냅샷: {snapshot['snapshot_id']}")
        prepared.append((dict(snapshot), message))

    db.execute("SAVEPOINT url_archive_reindex")
    try:
        db.execute("DELETE FROM archived_urls")
        count = 0
        for snapshot, message in prepared:
            count += _store_url_occurrences(db, snapshot, message)
        db.execute("RELEASE SAVEPOINT url_archive_reindex")
        return count
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT url_archive_reindex")
        db.execute("RELEASE SAVEPOINT url_archive_reindex")
        raise


def backfill_archive(db):
    """Idempotently archive current legacy news rows that have no snapshot."""
    completed = db.execute(
        "SELECT 1 FROM url_archive_meta WHERE key='legacy_news_backfill_complete'").fetchone()
    if completed:
        return 0
    rows = db.execute("""SELECT n.* FROM news n LEFT JOIN message_snapshots s
        ON s.chat_id=n.chat_id AND s.message_id=n.message_id
        WHERE s.snapshot_id IS NULL""").fetchall()
    count = 0
    for row in rows:
        item = dict(row)
        published = item.get("published_at") or f"{item['day']}T00:00:00+00:00"
        try:
            timestamp = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
        except ValueError:
            timestamp = int(item.get("version") or 0)
        message = {
            "chat": {"id": item["chat_id"], "title": item.get("channel") or item["chat_id"],
                     "type": "channel"},
            "message_id": item["message_id"], "date": timestamp,
            "edit_date": int(item.get("version") or timestamp), "text": item.get("text") or "",
            "_archive_published_at": published, "_archive_day": item.get("day") or published[:10],
            "_archive_telegram_url": item.get("url") or "", "_archive_source": "news_backfill",
        }
        archive_message(db, message)
        count += 1
    db.execute("INSERT OR REPLACE INTO url_archive_meta VALUES ('legacy_news_backfill_complete','1')")
    return count


def archived_rows(db, params=None):
    """Return a safe, paginated URL-occurrence view; never expose raw payload JSON."""
    params = params or {}
    scalar = lambda name, default="": (params.get(name, [default])[0]
                                        if isinstance(params.get(name, [default]), (list, tuple))
                                        else params.get(name, default))
    try:
        page = max(1, int(scalar("page", "1")))
        page_size = min(100, max(1, int(scalar("page_size", "50"))))
    except (TypeError, ValueError):
        raise ValueError("page와 page_size는 정수여야 합니다.")
    status = str(scalar("status")).casefold()
    status_active = {"active": "1", "history": "0", "all": "all"}.get(status, "")
    active_value = str(scalar("active", status_active or "1")).casefold()
    if active_value not in {"", "all", "0", "1", "true", "false"}:
        raise ValueError("active는 0, 1 또는 all이어야 합니다.")
    where, values = [], []
    if active_value not in {"", "all"}:
        where.append("active=?")
        values.append(1 if active_value in {"1", "true"} else 0)
    for keys, column in ((('date',), "day"), (("chat_id", "channel"), "chat_id")):
        value = next((str(scalar(key)).strip() for key in keys if str(scalar(key)).strip()), "")
        if value:
            where.append(f"{column}=?")
            values.append(value)
    query = str(scalar("q")).strip().casefold()
    if query:
        where.append("(LOWER(original_url) LIKE ? OR LOWER(title) LIKE ? OR LOWER(nearby_context) LIKE ?)")
        pattern = f"%{query}%"
        values.extend((pattern, pattern, pattern))
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = db.execute("SELECT COUNT(*) FROM archived_urls" + clause, values).fetchone()[0]
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    selected = db.execute("""SELECT snapshot_id,occurrence,chat_id,message_id,version,day,
        published_at,channel,telegram_url,original_url,canonical_url,title,title_source,
        verified_article_title,origin,entity_type,nearby_context,active
        FROM archived_urls""" + clause +
        " ORDER BY published_at DESC,message_id DESC,occurrence LIMIT ? OFFSET ?",
        (*values, page_size, (page - 1) * page_size)).fetchall()
    return {"items": [dict(row) for row in selected], "total": total, "page": page,
            "page_size": page_size, "total_pages": total_pages}


def archived_link_rows(db, hidden_only=False, active_only=True, *, message=None):
    """Return build_link_groups-compatible rows, including hidden text_link URLs."""
    where = []
    if hidden_only:
        where.append("entity_type='text_link'")
    if active_only:
        where.append("active=1")
    if message is not None:
        where.append("chat_id=? AND message_id=?")
    clause = " WHERE " + " AND ".join(where) if where else ""
    rows = db.execute("SELECT * FROM archived_urls" + clause +
                      " ORDER BY published_at,message_id,occurrence", message or ()).fetchall()
    return [{
        "chat_id": row["chat_id"], "message_id": row["message_id"],
        "item_index": 1_000_000 + row["occurrence"], "title": row["title"] or "링크 제목 미상",
        "excerpt": row["nearby_context"], "text": row["nearby_context"], "day": row["day"],
        "date_basis": "telegram", "topic": "general", "source_url": row["original_url"],
        "kind": "article", "channel": row["channel"], "url": row["telegram_url"],
        "published_at": row["published_at"], "telegram_day": row["day"],
        "archive_snapshot_id": row["snapshot_id"], "archive_origin": row["origin"],
    } for row in rows]
