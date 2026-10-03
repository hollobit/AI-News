"""Allowlisted public projections: never copy raw DB rows or operational payloads."""
import hashlib
import json
from pathlib import Path

ASSETS=('workspace-navigation.js','workspace-ui.css','public.html','public.js','public.css','public-data.js','workspace.js','workspace.css','news-network.js','news-network.css','risk-network.js','risk-network.css','observatory.html','observatory.js','observatory.css','observatory-search.js','public-navigation.js')
DATA_FILES=('site.json','site-manifest.json','build.json')+tuple(f'observatory-{days}-{mode}.json' for days in (14,30,90) for mode in ('default','expanded'))
PUBLIC_FILES=tuple(dict.fromkeys(
    ('knowledge.html','wiki-network.js','wiki-network-3d.js','wiki-network.css','three.module.js','three.core.js','three.LICENSE','knowledge.json','.nojekyll','README.md')
    +tuple('index.html' if name=='public.html' else name for name in ASSETS)+DATA_FILES))


def published_files(root):
    from public_data import data_files
    names=PUBLIC_FILES+data_files(root)
    if any((Path(root)/name).is_symlink() for name in names):
        raise ValueError('Public assets must not be symlinks')
    return names

def pick(data,keys):return {k:data[k] for k in keys.split() if k in data}
def identity(value):return hashlib.sha256(value.encode()).hexdigest()[:24]

def observation(raw):
    from export_wiki_site import public_url
    d=raw.get('data',{})
    if d.get('selection_version')!=2 or not d.get('days'):return {'unavailable':True}
    result=pick(d,'selection_version document_index_version comparison_days days limits method version comparison computed_at')
    result['nodes']=[pick(n,'id label kind origin count series evidence_by_day document_ids_by_day current previous') for n in d['nodes']]
    result['edges']=[pick(e,'id source target relation count series evidence_by_day current previous') for e in d['edges']]
    result['evidence']={key:dict(pick(e,'id day title briefing_title source_title title_origin'),url=public_url(e.get('url','')),
        document_id=identity(str(e.get('document_id',key)))) for key,e in d['evidence'].items()}
    if d.get('document_index_version')==1:
        result['documents']={key:dict(pick(e,'id day title briefing_title source_title title_origin'),url=public_url(e.get('url','')),
            document_id=identity(str(e.get('document_id',key)))) for key,e in d.get('documents',{}).items()}
    return result

def content(db, *, news_only=False, selected_items=None, include_excerpts=False):
    from export_wiki_site import public_url
    from source_titles import title_projection
    from improvement_selection import all_corpus_items, content_identity
    from baseline_graph import baseline_sources
    from arxiv_papers import _paper_rows
    from paper_graph import validated_paper_analysis
    from completion_quality import document_admission, message_text
    from graph_rag import validated_workflow_content
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    items=selected_items if selected_items is not None else (all_corpus_items(db) if {'news','articles','archived_urls'}<=tables else [])
    articles=[]; bykey={}; risks=[]
    for item in items:
        key=content_identity(item)
        from keyword_index import document_id
        entry=dict(id=identity(key),title=item.get('title') or '제목 없음',day=item.get('day',''),
            topic=item.get('topic',''),url=public_url(item.get('original_url') or item.get('source_url',''), db),analyses=[],observation_document_id=identity(document_id(item)))
        entry.update(title_projection(item))
        articles.append(entry);bykey[key]=entry
    baseline,_=baseline_sources(db,items)
    for source in baseline:
        key=next((n['name'] for n in source['result']['nodes'] if n['type']=='SourceDocument'),'')
        article=bykey.get(key)
        if not article:continue
        for n in source['result']['nodes']:
            if n['type']=='NewsSummary':article['analyses'].append(dict(kind='기본 분석',text=n['summary']))
    if {'corpus_completion_documents','strategic_workflow_runs','strategic_workflow_artifacts'}<=tables:
        current={content_identity(i):i for i in items};runs={};seen=set()
        rows=db.execute('''SELECT d.document_id,r.id,r.status,r.error,a.payload_json FROM corpus_completion_documents d
            JOIN strategic_workflow_runs r ON r.id=d.workflow_run_id
            JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final'
            WHERE d.status='complete' AND r.status='complete' AND r.error='' ORDER BY r.updated_at DESC''')
        for row in rows:
            key=row['document_id']; item=current.get(key)
            if item is None or key in seen:continue
            if row['id'] not in runs:
                report=json.loads(row['payload_json'])
                runs[row['id']]=(report,validated_workflow_content(report,row['id']))
            report,review=runs[row['id']]
            admission=document_admission(dict(row,results=report),item)
            if not admission['verified']:continue
            seen.add(key)
            own={e['id'] for e in report['evidence'] if e.get('url')==item.get('source_url') and
                 ((e.get('origin')=='telegram_excerpt' and e['text']==message_text(item)) or
                  (e.get('origin')=='fetched_url_excerpt' and e['text']==str((item.get('source_context') or {}).get('text') or '')[:3500]))}
            for claim in review.get('claims',[]):
                if claim['evidence_ids'] and set(claim['evidence_ids'])<=own:
                    bykey[key]['analyses'].append(dict(kind='심층 분석',title=claim['title'],text=claim['detail'],uncertainty=claim['uncertainty']))
            if admission['risk_reviewed']:
                from risk_analysis import validated_risk_content
                checked=validated_risk_content(report,row['id'])
                for risk in checked.get('risks',[]):
                    if risk.get('evidence_ids') and set(risk['evidence_ids'])<=own:
                        risks.append(dict(pick(risk,'title current_severity current_basis scenario assumptions uncertainty future_likelihood horizon mitigations'),
                            article_id=bykey[key]['id'],url=bykey[key]['url'],day=bykey[key]['day'],
                            analysis_at=report.get('completed_at','')))
    if news_only:
        return dict(news=articles, risks=risks)
    if include_excerpts:
        for item in items:
            article = bykey[content_identity(item)]
            source = item.get('source_context') or {}
            article['source_text'] = source.get('text', '') if source.get('status') == 'fetched' else ''
    papers=[]
    if 'arxiv_papers' in tables:
        analyses={r['paper_id']:dict(r) for r in db.execute('SELECT * FROM arxiv_paper_analyses')} if 'arxiv_paper_analyses' in tables else {}
        for item in _paper_rows(db):
            if item.get('metadata_status')!='fetched':continue
            result=validated_paper_analysis(analyses.get(item['paper_id'],{}),item)
            paper=dict(id=item['paper_id'],title=item['title'],day=(item.get('published') or '')[:10],
                url='https://arxiv.org/abs/'+item['paper_id'],provider=item.get('metadata_source',''),
                status='검토 완료' if result else '현재 공개 가능한 검토 결과 없음',claims=[])
            if result:
                paper['summary']=result['report'].get('summary','')
                paper['claims']=[pick(c,'title detail uncertainty') for c in result['report']['claims']]
            papers.append(paper)
    articles.sort(key=lambda a:a['day'],reverse=True)
    risk_graph={"nodes":[],"edges":[],"coverage":{},"method":{},"warnings":["공개 관계 지도 스냅샷이 아직 생성되지 않았습니다."]}
    try:
        from risk_graph import load_risk_graph
        raw_graph=load_risk_graph(db,{'risk_limit':['2000'],'edge_limit':['12000']})
        # The public risk list uses the source-news day. Add that same day to
        # the graph node so the date selector cannot disagree with the list
        # merely because the evidence record was ingested on another day.
        risk_days={risk.get('title'):risk.get('day') for risk in risks if risk.get('title') and risk.get('day')}
        for graph_node in raw_graph.get('nodes',[]):
            if graph_node.get('kind')!='risk':continue
            day=risk_days.get(graph_node.get('label') or graph_node.get('name'))
            if day:
                graph_node['day']=day
                graph_node['days']=sorted(set((graph_node.get('days') or [])+[day]))
        public_titles=set(risk_days)
        public_node_ids={node.get('id') for node in raw_graph.get('nodes',[])
                         if node.get('kind')!='risk' or node.get('label') in public_titles or node.get('name') in public_titles}
        raw_graph['nodes']=[node for node in raw_graph.get('nodes',[]) if node.get('id') in public_node_ids]
        raw_graph['edges']=[edge for edge in raw_graph.get('edges',[]) if edge.get('source') in public_node_ids and edge.get('target') in public_node_ids]
        raw_graph['coverage']=dict(raw_graph.get('coverage') or {},risks=sum(node.get('kind')=='risk' for node in raw_graph['nodes']),relationships=len(raw_graph['edges']))
        risk_graph={**pick(raw_graph,'coverage method warnings'),
            'nodes':[pick(n,'id label name type kind day days severity likelihood status summary weight similar_risk_count shared_evidence_count') for n in raw_graph.get('nodes',[])],
            'edges':[pick(e,'source target relation meaning similarity') for e in raw_graph.get('edges',[])]}
        risk_graph['warnings']=list(risk_graph.get('warnings') or [])+['공개용 스냅샷에는 원문 발췌·실행 식별자를 포함하지 않습니다.']
    except (RuntimeError,ValueError,KeyError):
        pass
    return dict(news=articles,papers=papers,risks=risks,risk_graph=risk_graph,coverage=dict(news=len(articles),reviewed_news=sum(bool(a['analyses']) for a in articles),
        papers=len(papers),reviewed_papers=sum(p['status']=='검토 완료' for p in papers)))

def write_site(db_path,target,root,stamp,data=None,*,assets=None,observations=None):
    import sqlite3
    if data is None:
        with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN');data=content(db)
    data['exported_at']=stamp
    (target/'site.json').write_text(json.dumps(data,ensure_ascii=False))
    for name in ASSETS:
        dest='index.html' if name=='public.html' else name
        text=assets[name].decode() if assets is not None else (root/'static'/name).read_text()
        if name=='observatory.html':
            import re
            text=text.replace('<html lang="ko">','<html lang="ko" data-public="true">')
            text=re.sub(r'(href|src)="/([^"?]+)"',lambda m:f'{m[1]}="'+(m[2] if '.' in m[2] else 'index.html?view='+m[2])+'"',text)
            text=text.replace('href="index.html?view=graph"','href="knowledge.html"').replace('href="index.html?view=observatory"','href="observatory.html"')
            text=text.replace('라이브 관측 지도','공개 관측 지도').replace('>GraphRAG</a>','>지식 관계 탐색</a>')
            text=text.replace('<script src="/paper-context.js"></script>','').replace('<script src="paper-context.js"></script>','')
        if dest.endswith('.html'):
            from site_templates import public_html
            text=public_html(text)
            text=text.replace('</body>','<script src="public-navigation.js"></script></body>')
        (target/dest).write_text(text)
    for days in (14,30,90):
        for mode in ('default','expanded'):
            path=Path(str(db_path)+'.observatory')/f'{days}-{mode}-v2.json'
            raw=observations[f'observatory-{days}-{mode}.json'] if observations is not None else (json.loads(path.read_text()) if path.exists() else {})
            (target/f'observatory-{days}-{mode}.json').write_text(json.dumps(observation(raw),ensure_ascii=False))
    return data['coverage']
