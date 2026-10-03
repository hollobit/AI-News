"""Read-only news search with existing publication gates for analysis results."""
from link_groups import canonical_url
from search_query import parse, matches, snippets


def source_bodies(db, urls):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'source_health', 'source_versions'} <= tables: return {}
    result = {}; urls = list(set(filter(None, urls)))
    for offset in range(0, len(urls), 400):
        batch = urls[offset:offset+400]
        result.update(db.execute('SELECT v.url,v.text FROM source_versions v JOIN source_health h '
            'ON h.url=v.url AND h.hash=v.hash WHERE v.url IN (' + ','.join('?' for _ in batch) + ')', batch))
    return result


def reviewed_fields(db, items, *, links=False):
    from public_site import content
    from improvement_selection import all_corpus_items
    urls = {canonical_url(i.get('canonical_url') if links else i.get('source_url')) for i in items}
    selected = all_corpus_items(db, source_urls=urls)
    public = content(db, news_only=True, selected_items=selected)
    fields = {}
    for article in public['news']:
        fields[article['id']] = [(a['kind'], '\n'.join(str(a.get(k) or '') for k in ('title','text','uncertainty')))
                                  for a in article['analyses']]
    for risk in public['risks']:
        values = [risk.get(k) for k in ('title','current_basis','scenario','uncertainty')]
        values += risk.get('assumptions', []) + risk.get('mitigations', [])
        fields.setdefault(risk['article_id'], []).append(('위험·조건부 분석', '\n'.join(v for v in values if v)))
    from public_site import identity
    from improvement_selection import content_identity
    from completion_quality import message_text
    return fields, {identity(content_identity(i)): message_text(i) for i in selected}


def filter_items(db, items, query, *, links=False):
    groups = parse(query)
    if not groups: return items
    from public_site import identity
    from improvement_selection import content_identity
    reviewed, contexts = reviewed_fields(db, items, links=links)
    bodies = source_bodies(db, [canonical_url(i.get('canonical_url') if links else i.get('source_url')) for i in items])
    result = []
    for item in items:
        url = canonical_url(item.get('canonical_url') if links else item.get('source_url'))
        source = item.get('source_context') or {}
        fields = [('제목', item.get('title', '')),
                  ('수집 내용', '\n'.join(m['text'] for m in item['mentions']) if links else item.get('text', '')),
                  ('원문 제목', source.get('title', '') if source.get('status') == 'fetched' else ''),
                  ('원문', bodies.get(url) or (source.get('text', '') if source.get('status') == 'fetched' else '')),
                  ('URL', url)]
        key = url if links else content_identity(item)
        from completion_quality import message_text
        from url_context import focus_url_context
        original = dict(item, title=item.get('briefing_title') or item.get('title', ''))
        if links or contexts.get(identity(key)) == message_text(focus_url_context(original, url)):
            fields += reviewed.get(identity(key), [])
        if matches(groups, [v for _, v in fields]):
            result.append(dict(item, search_matches=snippets(groups, fields)))
    return result
