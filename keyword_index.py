"""Persistent all-word index and bounded local keyword discovery."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import unicodedata

from link_groups import canonical_url, extract_links


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_GENERIC = {
    "관련", "기반", "기술", "대한", "통해", "위한", "이번", "있는", "하는", "했다",
    "한다", "소개", "뉴스", "소식", "발표", "공개", "링크", "자료", "내용", "새로운",
    "그리고", "하지만", "에서", "으로", "부터", "까지", "the", "and", "for", "from",
    "with", "this", "that", "news", "new", "about", "into", "using", "release", "released",
    "announced", "announcement", "article", "report", "update", "updated", "today",
    "http", "https", "www", "com", "org", "net", "html",
}
_ALIASES = {
    "openai": ("openai", "OpenAI"),
}
_PROMINENT_LIMIT = 6
_INDEX_VERSION = "3-source-excerpts"


def init_keyword_index(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS keyword_terms (
            keyword_id TEXT PRIMARY KEY,
            normalized TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            is_generic INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS keyword_documents (
            document_id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS keyword_records (
            record_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            day TEXT NOT NULL,
            content_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS record_keywords (
            record_id TEXT NOT NULL,
            keyword_id TEXT NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY(record_id, keyword_id)
        );
        CREATE INDEX IF NOT EXISTS keyword_records_document
            ON keyword_records(document_id, record_id);
        CREATE INDEX IF NOT EXISTS record_keywords_keyword
            ON record_keywords(keyword_id, record_id);
        CREATE TABLE IF NOT EXISTS keyword_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(keyword_terms)")}
    if "is_generic" not in columns:
        db.execute("ALTER TABLE keyword_terms ADD COLUMN is_generic INTEGER NOT NULL DEFAULT 0")
    db.execute("INSERT OR IGNORE INTO state VALUES ('keyword_source_revision','1')")


def mark_keyword_source_changed(db) -> str:
    row = db.execute("SELECT value FROM state WHERE key='keyword_source_revision'").fetchone()
    revision = str(int(row[0] if row else 0) + 1)
    db.execute("INSERT OR REPLACE INTO state VALUES ('keyword_source_revision',?)", (revision,))
    return revision


def _source_revision(db) -> str:
    row = db.execute("SELECT value FROM state WHERE key='keyword_source_revision'").fetchone()
    return str(row[0] if row else "0")


def _is_current(db, revision: str) -> bool:
    values = {row[0]: row[1] for row in db.execute(
        "SELECT key,value FROM keyword_index_meta WHERE key IN ('version','source_revision')")}
    return values.get("version") == _INDEX_VERSION and values.get("source_revision") == revision


def _mark_current(db, revision: str) -> None:
    db.executemany("INSERT OR REPLACE INTO keyword_index_meta VALUES (?,?)", [
        ("version", _INDEX_VERSION), ("source_revision", revision)])


def ensure_keyword_index(db, rows_factory) -> bool:
    """Refresh once per source revision, with a write lock and a post-lock recheck."""
    revision = _source_revision(db)
    if _is_current(db, revision):
        return False
    if db.in_transaction:
        # Respect a caller-owned transaction; its snapshot cannot be replaced.
        sync_keyword_index(db, rows_factory())
        _mark_current(db, revision)
        return True
    while True:
        revision = _source_revision(db)
        plan = _prepare_index(db, rows_factory())
        db.execute("BEGIN IMMEDIATE")
        try:
            latest = _source_revision(db)
            if _is_current(db, latest):
                db.commit()
                return False
            if latest != revision:
                db.rollback()
                continue
            sync_keyword_index(db, (), prepared=plan)
            _mark_current(db, latest)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise


def _clean(value) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _normalize_word(word: str) -> tuple[str, str]:
    display = _clean(word)
    normalized = display.casefold()
    return _ALIASES.get(normalized, (normalized, display))


def _words(title: str, text: str, urls: str) -> list[tuple[str, str, int, bool]]:
    """Return every distinct normalized word; generic status only affects prominence."""
    scores = Counter()
    labels = {}
    for value, weight in ((title, 3), (text, 1), (urls, 1)):
        for word in _WORD.findall(_clean(value)):
            normalized, label = _normalize_word(word)
            if not normalized:
                continue
            scores[normalized] += weight
            labels.setdefault(normalized, label)
    ranked = sorted(scores, key=lambda value: (-scores[value], value))
    return [(word, labels[word], scores[word],
             word in _GENERIC or word.isdecimal() or len(word) == 1)
            for word in ranked]


def document_id(item: dict) -> str:
    source = canonical_url(item.get("source_url") or "")
    if not source:
        source = next((canonical_url(url) for url in extract_links(item.get("text") or "")
                       if canonical_url(url)), "")
    material = ("url\0" + source) if source else "item\0{}\0{}\0{}".format(
        item.get("chat_id") or "", item.get("message_id") or "", item.get("item_index") or 0)
    return sha256(material.encode()).hexdigest()[:24]


def keyword_record_id(item: dict) -> str:
    material = "record\0{}\0{}\0{}".format(
        item.get("chat_id") or "", item.get("message_id") or "", item.get("item_index") or 0)
    return sha256(material.encode()).hexdigest()[:24]


def _record(item: dict) -> dict:
    urls = [str(item.get("source_url") or ""), *(item.get("embedded_urls") or []),
            *extract_links(item.get("text") or "")]
    evidence = {
        "title": _clean(item.get("title")), "text": _clean(item.get("text")),
        "urls": " ".join(url for url in urls if url),
    }
    source = item.get('source_context') or {}
    if source.get('status') == 'fetched':
        evidence['text'] += ' ' + _clean(source.get('title')) + ' ' + _clean(source.get('text'))
    return {
        "record_id": keyword_record_id(item), "document_id": document_id(item),
        "day": str(item.get("day") or ""),
        "content_hash": sha256(json.dumps(
            evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        **evidence,
    }


def _prepare_index(db, rows):
    records = [_record(dict(row)) for row in rows]
    existing = {row['record_id']: dict(row) for row in db.execute('SELECT * FROM keyword_records')}
    for record in records:
        old = existing.get(record['record_id'])
        if not old or any(old[key] != record[key] for key in ('document_id','day','content_hash')):
            record['_words'] = _words(record['title'],record['text'],record['urls'])
    grouped = defaultdict(list)
    for record in records:
        grouped[record['document_id']].append(record)
    documents = []
    for doc_id, members in grouped.items():
        days = [record['day'] for record in members if record['day']]
        hashes = sorted(record['content_hash'] for record in members)
        canonical = next((canonical_url(url) for record in members
                          for url in extract_links(record['urls']) if canonical_url(url)), '')
        documents.append((doc_id,canonical,min(days) if days else '',max(days) if days else '',
                          sha256('\0'.join(hashes).encode()).hexdigest()))
    return records, documents


def sync_keyword_index(db, rows, prepared=None) -> None:
    """Apply only changed records; preparation may run outside the writer lock."""
    records, documents = prepared if prepared is not None else _prepare_index(db, rows)
    active_record_ids = {record["record_id"] for record in records}
    existing_records = {row["record_id"]: dict(row) for row in db.execute(
        "SELECT * FROM keyword_records")}
    now = datetime.now(timezone.utc).isoformat()
    for record in records:
        old = existing_records.get(record["record_id"])
        if old and old["document_id"] == record["document_id"] \
                and old["day"] == record["day"] and old["content_hash"] == record["content_hash"]:
            continue
        db.execute("""INSERT INTO keyword_records VALUES (?,?,?,?)
            ON CONFLICT(record_id) DO UPDATE SET document_id=excluded.document_id,
            day=excluded.day,content_hash=excluded.content_hash""", tuple(record[key] for key in (
                "record_id", "document_id", "day", "content_hash")))
        db.execute("DELETE FROM record_keywords WHERE record_id=?", (record["record_id"],))
        for normalized, label, weight, is_generic in record.get("_words", ()):
            keyword_id = sha256(normalized.encode()).hexdigest()[:16]
            db.execute("""INSERT INTO keyword_terms
                (keyword_id,normalized,label,first_seen,first_observed_at,is_generic)
                VALUES (?,?,?,?,?,?) ON CONFLICT(keyword_id) DO UPDATE SET
                first_seen=MIN(keyword_terms.first_seen,excluded.first_seen),
                is_generic=MIN(keyword_terms.is_generic,excluded.is_generic)""",
                       (keyword_id, normalized, label, record["day"], now, int(is_generic)))
            db.execute("INSERT INTO record_keywords VALUES (?,?,?)",
                       (record["record_id"], keyword_id, weight))
    removed = set(existing_records) - active_record_ids
    if removed:
        db.executemany("DELETE FROM record_keywords WHERE record_id=?",
                       [(record_id,) for record_id in removed])
        db.executemany("DELETE FROM keyword_records WHERE record_id=?",
                       [(record_id,) for record_id in removed])

    existing_documents = {row['document_id']: tuple(row[key] for key in
        ('document_id','canonical_url','first_seen','last_seen','content_hash'))
        for row in db.execute('SELECT * FROM keyword_documents')}
    for values in documents:
        if existing_documents.get(values[0]) == values:
            continue
        db.execute("""INSERT INTO keyword_documents VALUES (?,?,?,?,?)
            ON CONFLICT(document_id) DO UPDATE SET canonical_url=excluded.canonical_url,
            first_seen=excluded.first_seen,last_seen=excluded.last_seen,
            content_hash=excluded.content_hash""", values)
    removed_documents = set(existing_documents) - {values[0] for values in documents}
    db.executemany('DELETE FROM keyword_documents WHERE document_id=?',
                   [(identity,) for identity in removed_documents])


def _metadata(db) -> dict[str, dict]:
    rows = db.execute("""SELECT t.keyword_id,t.label,t.first_seen,t.first_observed_at,
        t.is_generic,COUNT(DISTINCT r.document_id) AS document_count
        FROM keyword_terms t LEFT JOIN record_keywords k ON k.keyword_id=t.keyword_id
        LEFT JOIN keyword_records r ON r.record_id=k.record_id
        GROUP BY t.keyword_id,t.label,t.first_seen,t.first_observed_at,t.is_generic""").fetchall()
    return {row["keyword_id"]: dict(row) for row in rows}


def _term(metadata: dict, keyword_id: str, day: str, scope_count: int | None = None) -> dict:
    value = metadata[keyword_id]
    result = {
        "id": keyword_id, "label": value["label"], "first_seen": value["first_seen"],
        "first_observed_at": value["first_observed_at"],
        "document_count": value["document_count"],
        "is_new": bool(day and day != "all" and value["first_seen"] == day),
        "is_generic": bool(value["is_generic"]),
    }
    if scope_count is not None:
        result["scope_document_count"] = scope_count
    return result


def _record_mappings(db, record_ids=None) -> dict[str, list]:
    mappings = defaultdict(list)
    keys = list(dict.fromkeys(record_ids)) if record_ids is not None else None
    batches = [None] if keys is None else [keys[i:i+400] for i in range(0,len(keys),400)]
    for batch in batches:
        where = '' if batch is None else ' WHERE record_id IN ('+','.join('?' for _ in batch)+')'
        for row in db.execute('SELECT record_id,keyword_id,weight FROM record_keywords'+where+
                              ' ORDER BY record_id,weight DESC,keyword_id', batch or []):
            mappings[row['record_id']].append(row)
    return mappings


def annotate_items(db, items: list[dict]) -> list[dict]:
    metadata = _metadata(db)
    mappings = _record_mappings(db, [keyword_record_id(item) for item in items])
    result = []
    for original in items:
        item = dict(original)
        record_id = keyword_record_id(item)
        rows = [row for row in mappings.get(record_id, []) if row["keyword_id"] in metadata]
        prominent = [row for row in rows if not metadata[row["keyword_id"]]["is_generic"]] or rows
        item["keyword_record_id"] = record_id
        item["word_count"] = len(rows)
        item["keywords"] = [_term(metadata, row["keyword_id"], str(item.get("day") or ""))
                            for row in prominent[:_PROMINENT_LIMIT]]
        item["_all_keyword_ids"] = [row["keyword_id"] for row in rows]
        result.append(item)
    return result


def public_item(item: dict) -> dict:
    result = dict(item)
    result.pop("_all_keyword_ids", None)
    return result


def read_keyword_record(db, record_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{24}", str(record_id or "")):
        raise ValueError("키워드 기록 ID 형식이 올바르지 않습니다.")
    record = db.execute("SELECT * FROM keyword_records WHERE record_id=?", (record_id,)).fetchone()
    if not record:
        return None
    metadata = _metadata(db)
    rows = db.execute("""SELECT keyword_id,weight FROM record_keywords
        WHERE record_id=? ORDER BY weight DESC,keyword_id""", (record_id,)).fetchall()
    words = [_term(metadata, row["keyword_id"], record["day"])
             for row in rows if row["keyword_id"] in metadata]
    return {"keyword_record_id": record_id, "document_id": record["document_id"],
            "word_count": len(words), "keywords": words}


def keyword_discovery(db, items: list[dict], date: str, selected_keyword: str = "") -> dict:
    metadata = _metadata(db)
    document_words = defaultdict(set)
    for item in items:
        document_words[document_id(item)].update(item.get("_all_keyword_ids") or [])
    scope_counts = Counter(word for words in document_words.values() for word in words)
    salient = [key for key in scope_counts if not metadata[key]["is_generic"]]
    ranked = sorted(salient, key=lambda key: (
        -scope_counts[key], metadata[key]["label"].casefold(), key))
    pairs = Counter()
    default_candidates = set(ranked[:24])
    for words in document_words.values():
        if selected_keyword:
            if selected_keyword not in words:
                continue
            for neighbor in words - {selected_keyword}:
                pairs[tuple(sorted((selected_keyword, neighbor)))] += 1
        else:
            prominent = sorted(words & default_candidates)
            for index, source in enumerate(prominent):
                for target in prominent[index + 1:]:
                    pairs[(source, target)] += 1
    relationships = [{
        "source": source, "target": target,
        "source_label": metadata[source]["label"], "target_label": metadata[target]["label"],
        "document_count": count, "relation_type": "co_occurs",
    } for (source, target), count in sorted(
        pairs.items(), key=lambda value: (-value[1], value[0]))[:40]]
    selected = metadata.get(selected_keyword)
    return {
        "selected_keyword": (_term(metadata, selected_keyword, date, scope_counts[selected_keyword])
                             if selected and selected_keyword in scope_counts else None),
        "new_keywords": [_term(metadata, key, date, scope_counts[key]) for key in ranked
                         if date != "all" and metadata[key]["first_seen"] == date][:20],
        "top_keywords": [_term(metadata, key, date, scope_counts[key]) for key in ranked[:20]],
        "relationships": relationships,
        "scope_note": ("제목·본문·URL의 모든 단어를 로컬에서 색인했습니다. 대표 목록과 관계는 읽기 쉬운 "
                       "단어로 제한하며, 단어 선택 시 해당 단어의 연결을 계산합니다. 최초 등장일은 뉴스 "
                       "날짜, 최초 수집 시각은 실제 저장 시각입니다. 같은 URL의 수집 기록을 합쳐 단어의 "
                       "연결을 계산하며, 이 연결은 인과관계가 아닙니다."),
    }
