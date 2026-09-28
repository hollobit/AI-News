"""Archive-wide, duplicate-free scheduling and source-backed topic expansion."""
import hashlib
import json
import re
import unicodedata
from link_groups import canonical_url, extract_links
from strategic_value import evaluate_news
from url_context import focus_url_context


class SelectionBatch(list):
    def __init__(self, items=(), coverage=None):
        super().__init__(items)
        self.coverage = coverage or {}


def content_identity(item):
    url = canonical_url(item.get('source_url') or '')
    if url:
        return url
    text = unicodedata.normalize('NFKC', ' '.join(str(item.get(k) or '') for k in ('title', 'text')))
    text = re.sub(r'\s+', ' ', text).strip().casefold()
    return 'news:corpus_' + hashlib.sha256(text.encode()).hexdigest()[:24]


def all_corpus_items(db, source_urls=None):
    """Include visible articles, unparsed URL fragments and hidden Telegram links.

    Every explicit URL is a scheduling unit; URL-free text is deduplicated by
    normalized title/body. Repeated mentions remain in the archive. Queue items
    expose the latest representative, with a count of distinct mention contexts.
    """
    from app import joined_articles, unindexed_link_rows, hidden_link_rows
    from source_enrichment import attach_sources
    rows = [r for r in joined_articles(db) if r.get('source_origin')!='external_watch']
    rows.extend(unindexed_link_rows(db, rows))
    rows.extend(hidden_link_rows(db, rows))
    grouped, contexts, source_rows = {}, {}, 0
    for row in rows:
        urls = list(dict.fromkeys(canonical_url(url) for url in
                    [row.get('source_url') or '', *extract_links(row.get('text') or '')] if canonical_url(url)))
        for url in urls or ['']:
            if source_urls is not None and url not in source_urls:
                continue
            item = focus_url_context(row, url) if url else dict(row, source_url=url)
            identity = content_identity(item)
            source_rows += 1
            signature = hashlib.sha256(json.dumps([item.get('title'), item.get('text')], ensure_ascii=False).encode()).hexdigest()
            contexts.setdefault(identity, set()).add(signature)
            if identity not in grouped or (str(item.get('day') or ''), str(item.get('published_at') or '')) > (
                    str(grouped[identity].get('day') or ''), str(grouped[identity].get('published_at') or '')):
                item['id'] = identity.removeprefix('news:') if not url else 'corpus_' + hashlib.sha256(url.encode()).hexdigest()[:24]
                grouped[identity] = item
    items = attach_sources(db, list(grouped.values()))
    for item in items:
        identity = content_identity(item)
        item['corpus_identity'] = identity
        # One parsed article can contain several URLs with distinct fetched texts.
        # Give each in-memory scheduling unit its own morphology record identity.
        item['source_item_index'] = item.get('item_index')
        item['item_index'] = -int(hashlib.sha256(identity.encode()).hexdigest()[:15], 16)-1
        item['distinct_contexts'] = len(contexts[identity])
        item['selection_reason'] = '전체 텔레그램 뉴스 대기열 · 정규 URL/동일 본문 중복 제거'
        item['strategic_value'] = evaluate_news(item)
    items.sort(key=lambda item: (item['strategic_value']['score'], str(item.get('day') or ''), str(item.get('published_at') or ''), item['corpus_identity']), reverse=True)
    return SelectionBatch(items, {'total_unique': len(items), 'source_rows': source_rows,
                                  'duplicates_excluded': source_rows-len(items),
                                  'scope': 'all_telegram_news',
                                  'unit': '정규 URL 또는 URL 없는 동일 제목·본문',
                                  'text_scope': '대표 뉴스 문맥과 조회 가능한 원문 발췌; 반복 게시 문맥은 원문 보관함에 보존'})


def select_improvement_news(db, settings, tasks, seen_ids):
    """Prioritize new evidence; related topics influence order, never erase backlog."""
    from strategy import select_strategy_items
    if settings.get('full_corpus', True):
        batch = all_corpus_items(db)
    else:
        scope = settings.get('scope') or {}
        params = {k: v if isinstance(v, list) else [str(v)] for k, v in scope.items() if v}
        params.setdefault('date', ['all'])
        params.setdefault('sort', ['strategic'])
        items = select_strategy_items(db, params)
        dedup = {content_identity(item): item for item in reversed(items)}
        items = list(reversed(list(dedup.values())))
        batch = SelectionBatch(items, {'total_unique': len(items), 'source_rows': len(items),
                                      'duplicates_excluded': 0, 'scope': 'selected_news'})
    terms = {unicodedata.normalize('NFKC', str(term)).strip().casefold()
             for task in tasks for term in task.get('search_terms', [])[:6] if isinstance(term, str) and term.strip()}
    if terms:
        from morphology import keyword_records
        from keyword_index import keyword_record_id
        records = keyword_records(db, batch)
        for item in batch:
            observed = records.get(keyword_record_id(item), [])
            matched = [k['label'] for k in observed if k['label'].casefold() in terms or k['surface'].casefold() in terms]
            item['expansion_terms'] = list(dict.fromkeys(matched))[:6]
            if matched:
                item['selection_reason'] = '후속 과제의 원문 형태소 키워드와 연결: ' + ', '.join(item['expansion_terms'])
        # Reserve every other position for the main queue to prevent topic starvation.
        related = [item for item in batch if item.get('expansion_terms')]
        rest = [item for item in batch if not item.get('expansion_terms')]
        interleaved = []
        for i in range(max(len(related), len(rest))):
            if i < len(related): interleaved.append(related[i])
            if i < len(rest): interleaved.append(rest[i])
        batch[:] = interleaved
    seen = set(seen_ids)
    # Core examines changed successful excerpts on seen URLs after new URLs.
    batch.sort(key=lambda item: content_identity(item) in seen)
    return batch
