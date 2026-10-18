"""Reader projections joining admitted interpretations without generating new claims."""
import json
from collections import defaultdict
from urllib.parse import quote


def enrich_sources(db, sources):
    from risk_analysis import validated_risk_content
    from knowledge_wiki import read_wiki
    from strategy_trends import LENSES, matched_terms
    from evidence_corrections import risk_projection_token,active_risks
    corrections=risk_projection_token(db) if any(s['kind']=='workflow' and 'workspace_risk_corrections' not in s for s in sources) else ()
    additions=[]
    for source in sources:
        if source['kind'] != 'workflow': continue
        row=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(source['id'],)).fetchone()
        if not row: continue
        payload=json.loads(row[0]); content=validated_risk_content(payload,source['id'])
        if not content: continue
        admitted={e['id']:e for e in source['result']['evidence']}
        nodes=[];edges=[]
        active=active_risks(content['risks'],source['id'],source.get('workspace_risk_corrections',corrections))
        for i,risk in enumerate(content['risks']):
            if risk not in active:continue
            refs=risk['evidence_ids']
            if not refs or not set(refs)<=set(admitted): continue
            rid='risk:'+str(i); sid='scenario:'+str(i)
            nodes.extend([
                dict(id=rid,name=risk['title'],type='RiskAssessment',summary='분석 당시 관측: '+risk['current_basis']+' / 불확실성: '+risk['uncertainty'],evidence_ids=refs),
                dict(id=sid,name=risk['title']+' · 조건부 시나리오',type='ConditionalScenario',summary=risk['scenario']+' / 성립 조건: '+'; '.join(risk['assumptions'])+' / 관찰 지표: '+'; '.join(risk['observed_indicators'])+' / 반대 근거: '+'; '.join(risk.get('counter_evidence',[]))+' / 악화 신호: '+'; '.join(risk.get('escalation_signals',[]))+' / 불확실성: '+risk['uncertainty'],evidence_ids=refs)])
            edges.append(dict(source=rid,target=sid,relation='조건부 전개',meaning='독립 검토된 가정 기반 시나리오 · 발생 예측이나 인과 확정 아님',evidence_ids=refs))
        if nodes:additions.append(dict(kind='workflow',id=source['id']+':risks',row={},result=dict(nodes=nodes,edges=edges,evidence=list(admitted.values()))))
    view=read_wiki(db,include_details=True)
    for page in view['pages']:
        refs=[e['id'] for e in page['evidence']]
        additions.append(dict(kind='wiki',id=page['id'],row={},result=dict(nodes=[dict(id='page',name=page['title'],type='KnowledgePage',summary='\n'.join(c['text'] for c in page['claims']),evidence_ids=refs)],edges=[],evidence=page['evidence'])))
    # Topic membership is a lexical observation; it is not a reviewed conclusion.
    for source in sources:
        evidence=source['result'].get('evidence',[])
        for lens in LENSES:
            selected=[e for e in evidence if matched_terms({'title':e.get('title',''),'text':e.get('text','')},lens['terms'])]
            if selected:
                additions.append(dict(kind=source['kind'],id=source['id']+':topic:'+lens['id'],row={},result=dict(nodes=[dict(id='topic:'+lens['id'],name=lens['name'],type='StrategicTopic',summary=lens['subtitle']+' · 원문 표현에 따른 주제 분류',evidence_ids=[e['id'] for e in selected])],edges=[],evidence=selected)))
    from link_groups import canonical_url
    for addition in additions:
        result=addition['result']; originals=list(result['nodes'])
        for i,e in enumerate(result['evidence']):
            url=canonical_url(e.get('source_url') or e.get('url') or '')
            if not url:continue
            sid='source:'+str(i)
            result['nodes'].append(dict(id=sid,name=url,type='SourceDocument',summary=e.get('title',''),evidence_ids=[e['id']]))
            for n in originals:
                if e['id'] in n['evidence_ids']:
                    result['edges'].append(dict(source=n['id'],target=sid,relation='주제 표현 일치' if n['type']=='StrategicTopic' else '근거 인용',meaning='원자료 연결 · 동일 출처의 해석은 독립 근거가 아님',evidence_ids=[e['id']]))
    return sources+additions


LABELS={'news':'뉴스·원자료','strategy':'전략 주제·해석','risk':'위험','scenario':'조건부 시나리오','paper':'논문','knowledge':'지식'}


def category(node):
    return {'SourceDocument':'news','NewsSummary':'news','StrategicClaim':'strategy','StrategicTopic':'strategy','RiskAssessment':'risk','ConditionalScenario':'scenario','PaperClaim':'paper','KnowledgePage':'knowledge'}.get(node.get('type'),'knowledge')


def dossier(graph,query='',focus='',limit=8):
    if hasattr(graph, 'workspace_store'):
        from context_index import materialize
        graph = materialize(graph, query)
    from graph_retrieval import get_index
    index,_=get_index(graph)
    scores=index.lexical.search(query) if query else {}
    reasons={}; ranked={}
    for (kind,key),score in sorted(scores.items(),key=lambda p:(-p[1],p[0]))[:160]:
        if kind=='n':ranked[key]=max(ranked.get(key,0),score);reasons.setdefault(key,'검색 표현 일치 · 관련성 후보')
        else:
            for nid in sorted(index.refs[key])[:40]:
                ranked[nid]=max(ranked.get(nid,0),score);reasons.setdefault(nid,'근거 내용 일치 · 관련성 후보')
    if focus:
        if focus not in index.nodes:raise ValueError('선택 항목이 갱신되었거나 현재 범위에 없습니다. 다시 검색해 주세요.')
        ranked={focus:100000};reasons={focus:'선택한 중심 항목'}
        seed=index.nodes[focus]
        for ref in seed.get('evidence_ids',[])[:80]:
            for nid in sorted(index.refs[ref])[:100]:
                if nid==focus:continue
                ranked[nid]=ranked.get(nid,0)+10;reasons[nid]='같은 원문 발췌 인용 · 독립 출처나 인과관계 아님'
        for edge in index.adjacency[focus][:100]:
            nid=edge['target'] if edge['source']==focus else edge['source']
            ranked[nid]=ranked.get(nid,0)+20;reasons[nid]=edge['relation']+' · '+edge.get('meaning','')
        # Follow cited source documents once, never turn an arbitrary two-hop path into causality.
        bridges=[nid for nid in ranked if index.nodes[nid]['type']=='SourceDocument'][:12]
        for bridge in bridges:
            for edge in index.adjacency[bridge][:80]:
                nid=edge['target'] if edge['source']==bridge else edge['source']
                if nid not in ranked:
                    ranked[nid]=5;reasons[nid]='동일 원문 URL 연결 · 문맥과 인용 범위를 대조하세요'
    groups={key:[] for key in LABELS};counts=defaultdict(int);cards=[]
    for nid in sorted(ranked,key=lambda n:(-ranked[n],n)):
        n=index.nodes[nid];kind=category(n);counts[kind]+=1
        if len(groups[kind])>=limit:continue
        evidence=[index.evidence[r] for r in n.get('evidence_ids',[])[:6] if r in index.evidence]
        sources=[dict(id=e['id'],title=e.get('title',''),url=e.get('source_url',''),day=e.get('day',''),kind=e.get('source_kind',''),verification=e.get('verification','원자료 관측'),origin=e.get('evidence_origin',''),excerpt=e.get('text','')[:700]) for e in evidence]
        href='/graph?q='+quote(n['name'],safe='')
        if kind=='knowledge':
            records=[r for r in n.get('source_records',[]) if r.startswith('wiki:')]
            if records:href='/wiki?id='+quote(records[0][5:].rsplit(':',1)[0],safe='')
        if kind=='news' and sources and sources[0]['url']:href='/article?url='+quote(sources[0]['url'],safe='')
        if kind=='paper':href='/papers'
        if kind in ('risk','scenario'):href='/risks'
        card=dict(status_label=('현재 독립 검토된 지식' if n['type']=='KnowledgePage' else '원문 표현 기반 주제 분류' if n['type']=='StrategicTopic' else '조건부 해석 · 발생 예측 아님' if n['type']=='ConditionalScenario' else '독립 검토된 해석' if n['type'] in ('NewsSummary','StrategicClaim','RiskAssessment','PaperClaim') else '원자료·개념 관측'),id=nid,title=n.get('summary') if n['type']=='SourceDocument' and n.get('summary') else n['name'],summary=n.get('summary',''),type=n['type'],kind=kind,reason=reasons[nid],sources=sources,href=href,source_count=len(set(n.get('document_ids',[]))),source_truncated=len(n.get('evidence_ids',[]))>len(sources))
        groups[kind].append(card);cards.append(card)
    return dict(groups=groups,counts=dict(counts),labels=LABELS,focus=focus,query=query,coverage=graph.get('coverage',{}),limitations=graph.get('limitations',[]),scope='검토된 해석과 원자료 관측을 구분해 제공 · 검색 상위 후보에서 분야별 최대 8건 · 연결 없음은 관련 정보 부재를 뜻하지 않음')
