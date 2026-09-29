"""Connect already validated public analyses without inventing semantic relations."""
from task_lifecycle import checkpoint
import json
from public_site import identity


def expand(graph, corpus, observations=None):
    nodes = {n['id']: dict(n) for n in graph['nodes']}
    edges = {e['id']: dict(e) for e in graph['edges']}
    news_sources = {n['news_id']: n['id'] for n in nodes.values() if n.get('news_id')}
    linked_news = linked_papers = 0

    def analysis(sid, values):
        for value in values:
            checkpoint()
            text = value.get('text') or value.get('detail') or ''
            if not text:
                continue
            cid = 'analysis:' + identity(sid + '\0' + json.dumps(value, sort_keys=True, ensure_ascii=False))
            nodes[cid] = dict(id=cid, type='claim', title=value.get('title') or text,
                analysis_text=text, uncertainty=value.get('uncertainty', ''),
                source_ids=[sid], status='reviewed', scope='현재 입력·독립 검토를 통과한 문서별 분석 · 위키 종합 관계와 구분')
            eid = 'analysis-link:' + identity(sid + cid)
            edges[eid] = dict(id=eid, source=sid, target=cid, kind='reviewed_analysis',
                layer='provenance', text=value.get('kind', '논문 분석'), source_ids=[sid])

    document_sources = {}
    for article in corpus['news']:
        checkpoint()
        if not article.get('analyses'):
            continue
        sid = news_sources.get(article['id'], 'source:news:' + article['id'])
        nodes.setdefault(sid, dict(id=sid, type='source', title=article['title'],
            url=article['url'], news_id=article['id'], day=article['day'],
            status='news_source', scope='검토된 뉴스 분석의 원자료 · 원보도 자체의 사실성 보증 아님'))
        analysis(sid, article['analyses'])
        document_sources[article.get('observation_document_id')] = sid
        linked_news += 1
    for paper in corpus['papers']:
        checkpoint()
        if paper.get('status') != '검토 완료':
            continue
        sid = 'source:paper:' + paper['id']
        nodes.setdefault(sid, dict(id=sid, type='source', title=paper['title'],
            url=paper['url'], day=paper['day'], status='paper_source',
            scope='검토된 논문 분석 · 초록 근거와 전문 검증은 다름'))
        analysis(sid, paper.get('claims', []))
        linked_papers += 1
    observed = observations or {}
    documents = observed.get('documents', {})
    for node in observed.get('nodes', []):
        checkpoint()
        sources = {document_sources.get(documents.get(key, {}).get('document_id'))
                   for day in node.get('document_ids_by_day', []) for key in day}
        sources.discard(None)
        if not sources:
            continue
        nid = 'news-observed:' + node['id']
        nodes[nid] = dict(id=nid, type='topic' if node['kind']=='topic' else 'concept',
            title=node['label'], source_ids=sorted(sources),
            scope='90일 문서별 공동 관측 · 의미 관계·인과관계 검증 아님')
        for sid in sorted(sources):
            checkpoint()
            eid = 'observation-link:' + identity(sid + nid)
            edges[eid] = dict(id=eid, source=sid, target=nid, kind='observed_in_document',
                layer='recommendation', text='같은 문서에 관측된 표현 · 인과관계 아님', source_ids=[sid])
    graph.update(nodes=list(nodes.values()), edges=list(edges.values()))
    graph['coverage'].update(total_news=len(corpus['news']), analyzed_news=linked_news,
        linked_news=linked_news, linked_papers=linked_papers,
        graph_nodes=len(nodes), graph_sources=sum(n['type']=='source' for n in nodes.values()),
        all_reviewed_documents_connected=True)
    graph['method'] += ' 전체 검토 문서의 분석 연결을 포함합니다. 문서 연결과 종합 위키 편찬 완료는 다릅니다.'
    return graph
