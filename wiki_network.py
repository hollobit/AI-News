"""Shared source/claim identities over current, independently reviewed editions.

This is a projection, not a new source of truth. Rebuilding it makes no LLM
calls, and invalid editions cannot leak through a persisted graph cache.
"""
from collections import defaultdict
from itertools import combinations
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import quote
from bulk_baseline import digest
from link_groups import canonical_url
from wiki_sources import exists

ROOT = Path(__file__).resolve().parent


def source_id(source):
    dep = source.get('dependency', {})
    if dep.get('kind') == 'paper':
        return 'source:paper:' + str(dep['key'])
    url = canonical_url(source.get('source_url') or '')
    return 'source:' + digest(url or ['news', dep.get('key'), source.get('id')])[:24]


def page_ids(view):
    """Only explicit user identity links merge pages; names are never sufficient."""
    parent = {p['id']: p['id'] for p in view['pages']}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    for alias in view.get('aliases', []):
        a, b = alias['source'], alias['target']
        if a in parent and b in parent:
            a, b = find(a), find(b)
            parent[max(a, b)] = min(a, b)
    return {p: 'page:' + digest(find(p))[:24] for p in parent}


def project(view):
    nodes, edges, sources = {}, {}, {}
    mapping = page_ids(view)
    shared = defaultdict(set)
    def edge(a, b, kind, layer, text='', refs=None):
        key = digest([a, b, kind, text])[:24]
        edges[key] = dict(id=key, source=a, target=b, kind=kind, layer=layer,
                          text=text, source_ids=sorted(set(refs or [])))
    for page in view['pages']:
        identity = mapping[page['id']]
        node = nodes.setdefault(identity, dict(id=identity, type=page['kind'], title=page['title'],
            page_ids=[], topics=[], source_ids=[], claims=[], href='/wiki?id='+page['id'], status='reviewed'))
        node['page_ids'].append(page['id'])
        node['topics'] = sorted(set(node['topics'] + [page['topic']]))
        refs = {}
        for source in page['evidence']:
            sid = source_id(source)
            refs[source['id']] = sid
            shared[sid].add(identity)
            sources.setdefault(sid, source)
            nodes.setdefault(sid, dict(id=sid, type='source', title=source['title'],
                origin=source['origin'], scope=source['scope'], day=source.get('day', ''),
                url=source.get('source_url', ''), status='original_source_not_fact_checked',
                version=digest(source['dependency']), source_ids=[sid],
                href=('/papers?paper='+str(source['dependency']['key']) if source['dependency']['kind']=='paper'
                      else '/article?url='+quote(source.get('source_url',''), safe=''))))
            edge(identity, sid, 'cites', 'provenance', refs=[sid])
        node['source_ids'] = sorted(set(node['source_ids']) | set(refs.values()))
        for claim in page['claims']:
            cited = sorted({refs[r] for r in claim['evidence_ids']})
            cid = 'claim:' + digest([claim['text'], claim['kind'], cited])[:24]
            nodes.setdefault(cid, dict(id=cid, type='claim', title=claim['text'],
                claim_kind=claim['kind'], source_ids=cited, status='reviewed', href='/wiki?id='+page['id']))
            if cid not in node['claims']:node['claims'].append(cid)
            edge(identity, cid, 'states', 'provenance', refs=cited)
            for sid in cited:edge(cid, sid, 'cites', 'provenance', refs=[sid])
    for link in view['links']:
        if link['source'] in mapping and link['target'] in mapping:
            page = next(p for p in view['pages'] if p['id']==link['source'])
            refs = {s['id']:source_id(s) for s in page['evidence']}
            edge(mapping[link['source']], mapping[link['target']], link['relation'], 'semantic',
                 link['text'], [refs[r] for r in link['evidence_ids'] if r in refs])
    # Shared source is a recommendation, never an independently verified relation.
    pairs = defaultdict(set)
    for sid, pages in shared.items():
        for a, b in combinations(sorted(pages), 2):pairs[a,b].add(sid)
    for (a,b), refs in sorted(pairs.items(), key=lambda x:(-len(x[1]), x[0]))[:500]:
        if a != b:edge(a,b,'shared_source','recommendation','같은 원자료를 인용한 페이지 · 의미 관계 검증 아님',refs)
    return dict(nodes=list(nodes.values()), edges=list(edges.values()), page_ids=mapping,
                sources=sources, version=digest([list(nodes.values()),list(edges.values())]))


def lint(view, graph):
    issues = []
    for topic in view['topics']:
        if topic['status'] != 'current':
            issues.append(dict(kind=topic['status'], target=topic['id'],
                message='현재 공개 가능한 검토 판이 없습니다.', job_status=topic['job_status']))
    labels = defaultdict(list)
    for node in graph['nodes']:
        if node['type'] in ('entity','concept','event'):
            labels[unicodedata.normalize('NFKC',node['title']).casefold()].append(node)
    for candidates in labels.values():
        if len(candidates)>1:
            issues.append(dict(kind='possible_duplicate', target=candidates[0]['id'],
                related=[n['id'] for n in candidates], message='동일 표기 후보입니다. 동명이인 여부를 확인해야 하며 자동 병합하지 않았습니다.'))
    semantic = {e['source'] for e in graph['edges'] if e['layer']=='semantic'} | {e['target'] for e in graph['edges'] if e['layer']=='semantic'}
    for node in graph['nodes']:
        if node['type'] not in ('source','claim') and node['id'] not in semantic:
            issues.append(dict(kind='isolated',target=node['id'],message='검토된 의미 관계가 없는 페이지입니다.'))
    for edge in graph['edges']:
        if edge['kind']=='contrasts':
            issues.append(dict(kind='tension', target=edge['source'], related=[edge['target']],
                message=edge['text']+' (조건 차이인지 실제 모순인지 확인)'))
    return issues


def read_network(db, identity='', query='', layer='all', limit=100, source_url='', paper_id=''):
    from knowledge_wiki import read_wiki
    view = read_wiki(db, include_details=True)
    graph = project(view)
    if source_url:identity='source:'+digest(canonical_url(source_url))[:24]
    if paper_id:identity='source:paper:'+paper_id
    if layer not in ('all','provenance','semantic','recommendation'):
        raise ValueError('지원하지 않는 관계 층입니다.')
    limit = max(10, min(500, int(limit)))
    identity = graph['page_ids'].get(identity, identity)
    nodes = list(graph['nodes'])
    byid = {n['id']:n for n in nodes}
    edges = [e for e in graph['edges'] if layer=='all' or e['layer']==layer]
    if layer != 'all':
        connected = {endpoint for e in edges for endpoint in (e['source'], e['target'])}
        if identity:connected.add(identity)
        nodes = [n for n in nodes if n['id'] in connected]
    if identity:
        selected = {identity}
        for e in edges:
            if identity in (e['source'], e['target']):selected.update((e['source'],e['target']))
        nodes = [n for n in nodes if n['id'] in selected]
    if query and not identity:
        nodes = [n for n in nodes if query.casefold() in n['title'].casefold()]
    total = len(nodes)
    nodes.sort(key=lambda n:(n['id']!=identity, n['type'] in ('source','claim'), n['title'],n['id']))
    nodes = nodes[:limit]
    allowed = {n['id'] for n in nodes}
    detail = byid.get(identity)
    if detail:
        detail = dict(detail, connections=[dict(e, other=byid[e['target'] if e['source']==identity else e['source']])
            for e in graph['edges'] if identity in (e['source'],e['target'])])
    elif identity:
        detail=dict(id=identity,type='source',title='현재 연결된 검토 지식 없음',
                    scope='미분석·재검토·위키 선정 범위 밖의 자료일 수 있습니다. 관계를 추측해 표시하지 않습니다.',connections=[])
    return dict(nodes=nodes, edges=[e for e in edges if e['source'] in allowed and e['target'] in allowed],
        detail=detail, issues=lint(view,graph), total=total, shown=len(nodes), limit=limit,
        version=graph['version'], topics=view['topics'],
        coverage=dict(pages=len(view['pages']), claims=sum(n['type']=='claim' for n in graph['nodes']),
                      cited_sources=len(graph['sources']), all_sources_compiled=False),
        method='현재 입력과 검토가 일치하는 지식만 연결합니다. 인용·의미 관계·탐색 추천은 서로 다른 층입니다. 전체 뉴스 분석 완료를 뜻하지 않습니다.')


def export_vault(db):
    from knowledge_wiki import read_wiki
    from wiki_registry import export_markdown
    view = read_wiki(db, include_details=True)
    graph = project(view)
    files = {}
    paths = {p['id']:'wiki/'+p['kind']+'/'+digest(p['id'])[:24] for p in view['pages']}
    source_paths = {sid:'raw/sources/'+digest(sid)[:24] for sid in graph['sources']}
    def frontmatter(data):
        # JSON is valid YAML; no untrusted filename or YAML interpolation.
        return '---\n'+ '\n'.join(k+': '+json.dumps(v,ensure_ascii=False) for k,v in data.items())+'\n---\n\n'
    def text(value):
        import html
        return re.sub(r'([\\`*_{}\[\]()#+!|>])',r'\\\1',html.escape(str(value),quote=False))
    for page in view['pages']:
        refs = sorted({source_id(s) for s in page['evidence']})
        content = export_markdown(dict(pages=[page],links=[]))
        content += '\n## 연결\n\n'
        for link in view['links']:
            if page['id'] not in (link['source'],link['target']):continue
            other = link['target'] if page['id']==link['source'] else link['source']
            content += '- [['+paths[other]+']] — '+text(link['relation']+': '+link['text'])+'\n'
        content += '\n## 보관 원근거\n\n'+'\n'.join('- [['+source_paths[r]+']]' for r in refs)+'\n'
        files[paths[page['id']]+'.md'] = frontmatter(dict(id=graph['page_ids'][page['id']],
            title=page['title'],type=page['kind'],revision=page['revision'],sources=refs)) + content
    for sid, source in graph['sources'].items():
        files[source_paths[sid]+'.md'] = frontmatter(dict(id=sid,type='source',origin=source['origin'],
            source_url=source.get('source_url',''),version=digest(source['dependency'])))+'# '+text(source['title'])+'\n\n'+text(source['scope'])+'\n\n'+text(source['text'])+'\n'
    files['wiki/index.md'] = '# 지식 위키 목차\n\n'+'\n'.join('- [['+paths[p['id']]+']]' for p in view['pages'])+'\n'
    files['wiki/log.md'] = '# 작업 이력\n\n과거 판은 현재 지식으로 내보내지 않습니다.\n\n'+'\n'.join(
        '- '+text(str(r['id'])+' '+r['created_at']+' '+r['topic']+' '+r['status'])
        for r in db.execute('SELECT id,topic,status,created_at FROM wiki_revisions ORDER BY id'))+'\n'
    files['purpose.md'] = (ROOT/'WIKI_PURPOSE.md').read_text()
    files['schema.md'] = (ROOT/'WIKI_SCHEMA.md').read_text()
    files['manifest.json'] = json.dumps(dict(version=graph['version'],pages=len(view['pages']),
        sources=len(graph['sources']),scope='Current reviewed pages and cited excerpts; not a complete corpus backup.'),ensure_ascii=False,indent=2)
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,content in files.items():archive.writestr(name,content)
    return output.getvalue()
