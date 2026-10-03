"""Read models for news; parsing and evidence identity live in the repository."""
import re
from collections import Counter
from datetime import datetime
from news_repository import (joined_articles, unique_articles, KST, TOPICS, CONTENT_TYPES, article_key)
from keyword_index import annotate_items, keyword_discovery, public_item, keyword_record_id


def _keyword_membership(db, items):
    """Filter/discovery need membership, not every off-screen card's term counts."""
    records = {keyword_record_id(item) for item in items}
    mappings = {}
    records = sorted(records)
    for offset in range(0, len(records), 400):
        batch = records[offset:offset + 400]
        for row in db.execute(
            'SELECT k.record_id,k.keyword_id FROM record_keywords k '
            'JOIN keyword_terms t ON t.keyword_id=k.keyword_id '
            'WHERE k.record_id IN (' + ','.join('?' for _ in batch) + ') '
            'ORDER BY k.record_id,k.weight DESC,k.keyword_id', batch):
            mappings.setdefault(row['record_id'], []).append(row['keyword_id'])
    return [dict(item, _all_keyword_ids=mappings.get(keyword_record_id(item), []))
            for item in items]


def read_news(db, params, *, include_discovery=True):
    from source_projection import news_dates, joined
    dates = news_dates(db)
    rows = None
    if dates is None:
        rows = joined_articles(db)
        dates_count = Counter(item["day"] for item in unique_articles(rows))
        dates = [{"date": day, "count": count} for day, count in sorted(dates_count.items(), reverse=True)]
    day = params.get("date", [dates[0]["date"] if dates else datetime.now(KST).date().isoformat()])[0]
    if day != "all":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
        datetime.strptime(day, "%Y-%m-%d")
    if rows is None:
        rows = joined(db, None if day == 'all' else day)
        if rows is None: rows = joined_articles(db)
    query = params.get("q", [""])[0].strip()
    channel = params.get("channel", [""])[0]
    topic = params.get("topic", [""])[0]
    content_type = params.get("content_type", [""])[0]
    keyword = params.get("keyword", [""])[0]
    if keyword and not re.fullmatch(r"[0-9a-f]{16}", keyword):
        raise ValueError("키워드 ID 형식이 올바르지 않습니다.")
    filtered = unique_articles(
        row for row in rows if (day == "all" or row["day"] == day)
        and (not channel or str(row["chat_id"]) == channel))
    if query:
        from news_search import filter_items
        filtered = filter_items(db, filtered, query)
    detail_id = params.get('detail_id', [''])[0]
    if detail_id and not re.fullmatch(r'[0-9a-f]{64}', detail_id):
        raise ValueError('Invalid article identity')
    for item in filtered: item['detail_id'] = article_key(item)
    if detail_id: filtered = [item for item in filtered if item['detail_id']==detail_id]
    filtered = _keyword_membership(db, filtered)
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
    total = len(items)
    page_size = max(1, min(200, int(params.get('page_size', ['100'])[0]))) if 'page_size' in params else None
    page = max(1, int(params.get('page', ['1'])[0]))
    discovery = keyword_discovery(db, items, day, keyword) if include_discovery else None
    if page_size:
        items = items[(page-1)*page_size:page*page_size]
    items = annotate_items(db, items)
    from source_titles import title_projection
    items = [public_item(dict(item, **title_projection(item))) for item in items]
    if params.get('compact', [''])[0] == '1':
        items = [dict({k:v for k,v in item.items() if k not in {'text','source_context'}},
                      has_full_text=bool(item.get('text') and item.get('text')!=item.get('excerpt'))) for item in items]
    return {"total":total,"page":page,"page_size":page_size,"date": day, "dates": dates, "channels": channels,
            "topics": topics, "types": [{"id": key, "title": title, "count": type_counts[key]}
                                           for key, title in CONTENT_TYPES.items()],
            "items": items,
            "keyword_discovery": discovery,
            "raw_message_count": db.execute("SELECT COUNT(*) FROM news").fetchone()[0],
            "demo": db.execute("SELECT 1 FROM state WHERE key='demo'").fetchone() is not None}
