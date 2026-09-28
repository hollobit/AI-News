"""Read-only strategic workbench view over collected and analyzed evidence."""
from collections import Counter
import json
from strategic_value import DOMAINS
from sector_taxonomy import sector_counts


def focused_items(items):
    from url_context import focus_url_context
    return [focus_url_context(item, item['source_url'])
            if item.get('source_url') and not item.get('url_context') else item for item in items]


def select_strategy_items(db, params, items=None, records=None, precomputed=False, registry=None, membership_only=False):
    """Use the same explainable priority and filters for display and actions."""
    from app import read_news
    from strategy_trends import filter_lens, filter_strategic_keyword
    from strategic_value import DOMAINS, evaluate_news
    if items is None:
        items = read_news(db, params)['items']
    items = focused_items(items)
    lens_id = params.get('lens', [''])[0]
    if lens_id.startswith('signal-group:'):
        from monitoring_groups import filter_group
        items=filter_group(db,items,lens_id,records,registry=registry)
        items=filter_lens(items,'',params.get('terms',[''])[0])
    elif lens_id.startswith(('dynamic:', 'dynamic-signal:', 'manual:', 'cooccurrence:')):
        from dynamic_strategy import filter_dynamic_lens
        items = filter_dynamic_lens(db, items, lens_id, records,registry=registry)
        items = filter_lens(items, '', params.get('terms', [''])[0])
    else:
        if lens_id and db.execute("SELECT 1 FROM sqlite_master WHERE name='dynamic_topic_registry'").fetchone():
            excluded = db.execute('SELECT excluded FROM dynamic_topic_registry WHERE id=?',(lens_id,)).fetchone()
            if excluded and excluded[0]:
                raise ValueError('제외된 전략 주제입니다. 관리 목록에서 복원해 주세요.')
        items = filter_lens(items, lens_id, params.get('terms', [''])[0])
    items = filter_strategic_keyword(db, items, params, records=records)
    if membership_only:
        if any(params.get(k) for k in ('sector','impact','sort')):
            raise ValueError('소속 조회에는 분야·영향·정렬 옵션을 사용할 수 없습니다.')
        return items
    from sector_taxonomy import filter_sector, classify_sectors, SECTORS
    if precomputed:
        sector = params.get('sector', [''])[0]
        if sector and sector not in {entry['id'] for entry in SECTORS}:
            raise ValueError('알 수 없는 응용 분야입니다.')
        items = [dict(item, sectors=item['sectors'] if 'sectors' in item else classify_sectors(item)) for item in items]
        if sector:
            items = [item for item in items if any(entry['id']==sector for entry in item['sectors'])]
    else:
        items = filter_sector(items, params.get('sector', [''])[0])
    domain = params.get('impact', [''])[0]
    if domain and domain not in {d['id'] for d in DOMAINS}:
        raise ValueError('알 수 없는 영향 분야입니다.')
    order = params.get('sort', ['latest'])[0]
    if order not in ('strategic', 'latest'):
        raise ValueError('알 수 없는 정렬입니다.')
    items = [dict(item, strategic_value=item['strategic_value'] if precomputed and 'strategic_value' in item else evaluate_news(item)) for item in items]
    if domain:
        items = [item for item in items if any(d['id'] == domain for d in item['strategic_value']['domains'])]
    items.sort(key=lambda item: (str(item.get('day') or ''), str(item.get('published_at') or '')), reverse=True)
    if order == 'strategic':
        items.sort(key=lambda item: item['strategic_value']['score'], reverse=True)
    return items


def read_strategy(db, params, sources):
    """Return observed coverage and saved interpretations without fabricating insights."""
    from app import read_news, link_groups_for
    from graph_rag import load_integrated_graph
    params = dict(params)
    params.setdefault('date', ['all'])
    params.setdefault('sort', ['strategic'])
    news = read_news(db, params)
    news['items'] = focused_items(news['items'])
    from strategy_trends import filter_lens, trend_metrics
    corpus = read_news(db, {'date': ['all']}) if params.get('date') != ['all'] or any(params.get(k) for k in ('q', 'topic', 'keyword')) else news
    corpus['items'] = focused_items(corpus['items'])
    from morphology import keyword_records
    from keyword_index import keyword_record_id
    morph = keyword_records(db, corpus['items'])
    trends = trend_metrics(db, corpus['items'], records=morph)
    from dynamic_strategy import apply_dynamic_trends
    trends = apply_dynamic_trends(db, corpus['items'], morph, trends)
    items = select_strategy_items(db, params, news['items'], records=morph)
    items = [dict(item, strategic_keywords=morph.get(keyword_record_id(item), [])) for item in items]
    if params.get('strategic_keyword'):
        target = params['strategic_keyword'][0]
        items = [item for item in items if any(term['id'] == target for term in item['strategic_keywords'])]
    groups = link_groups_for(db)
    group_ids = {group['canonical_url']: group['id'] for group in groups}
    from link_groups import canonical_url
    analyses = {}
    runs = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='research_runs'").fetchone():
        for row in db.execute('SELECT id,status,created_at,total_documents,processed_documents,successful_documents,failed_documents,report_json,error FROM research_runs ORDER BY created_at DESC LIMIT 12'):
            run = dict(row)
            run['report'] = json.loads(run.pop('report_json') or 'null')
            runs.append(run)
        for row in db.execute("""SELECT d.canonical_url,d.analysis_json,d.run_id,d.doc_id,d.source_status
            FROM research_documents d JOIN research_runs r ON r.id=d.run_id
            WHERE d.status='complete' ORDER BY r.created_at DESC"""):
            url = row['canonical_url']
            if url and url not in analyses:
                analyses[url] = dict(json.loads(row['analysis_json']), run_id=row['run_id'], source_status=row['source_status'])
    selected = []
    from bulk_baseline import read_baseline
    from recursive_improvement import news_identity
    baselines = read_baseline(db, items[:80])
    for item in items[:80]:
        url = canonical_url(item.get('source_url') or '')
        selected.append(dict(item, group_id=group_ids.get(url), strategic_analysis=analyses.get(url), base_analysis=baselines.get(news_identity(item))))
    graph = load_integrated_graph(db, {'max_nodes': ['24']})
    heartbeat = db.execute("SELECT value FROM state WHERE key='collector_last_success'").fetchone()
    last = db.execute('SELECT MAX(published_at) FROM news').fetchone()[0]
    types = Counter(item['content_type'] for item in items)
    return {'items': selected, 'total': len(items), 'shown': len(selected),
            'dates': news['dates'], 'topics': news['topics'], 'types': news['types'],
            'keyword_discovery': news['keyword_discovery'],
            'sectors': sector_counts(items),
            'impact_domains': [dict(id=d['id'], label=d['label'], count=sum(any(x['id']==d['id'] for x in item['strategic_value']['domains']) for item in items)) for d in DOMAINS],
            'raw_messages': news['raw_message_count'], 'url_groups': len(groups),
            'sources': sources, 'analysis_url_count': len(analyses), 'runs': runs,
            'graph': graph, 'trends': trends, 'collector': {'last_success': heartbeat[0] if heartbeat else None, 'last_message': last},
            'type_counts': dict(types), 'scope': {key: value[0] for key, value in params.items()}}
