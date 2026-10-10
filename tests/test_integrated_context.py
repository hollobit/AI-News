import sqlite3
import pytest
from graph_rag import GraphResult
from integrated_context import dossier, enrich_sources


def graph():
    evidence=[dict(id='e',title='의료 AI 임상 근거',text='의료 AI 진단',source_url='https://example.org/a',day='2026-10-10',source_kind='workflow')]
    nodes=[dict(id=k,name='의료 AI '+k,type=t,summary='조건과 불확실성',evidence_ids=['e'],document_ids=['doc']) for k,t in [('news','SourceDocument'),('risk','RiskAssessment'),('scenario','ConditionalScenario'),('paper','PaperClaim'),('wiki','KnowledgePage'),('topic','StrategicTopic')]]
    edges=[dict(source='risk',target='scenario',relation='조건부 전개',meaning='가정',evidence_ids=['e'])]
    return GraphResult(dict(nodes=nodes,edges=edges,evidence=evidence))


def test_six_domains_and_distinct_relationship_reasons():
    result=dossier(graph(),'의료 AI')
    assert all(result['groups'].values())
    assert all('후보' in c['reason'] for rows in result['groups'].values() for c in rows)
    focused=dossier(graph(),focus='risk')
    assert '조건부 전개' in focused['groups']['scenario'][0]['reason']
    assert '같은 원문' in focused['groups']['paper'][0]['reason']
    assert focused['groups']['risk'][0]['source_count']==1


def test_missing_focus_does_not_show_unrelated_items():
    with pytest.raises(ValueError):dossier(graph(),focus='stale-id')
    assert not any(dossier(graph(),'unrelated-nothing')['groups'].values())


def test_per_category_limit_and_no_mutation():
    g=graph();before=repr(g)
    result=dossier(g,'의료',limit=1)
    assert all(len(rows)<=1 for rows in result['groups'].values())
    assert repr(g)==before


def test_enrichment_requires_review_gate_and_admitted_evidence(monkeypatch):
    db=sqlite3.connect(':memory:')
    db.execute('create table strategic_workflow_artifacts(run_id,stage,payload_json)')
    db.execute("insert into strategic_workflow_artifacts values('r','final','{}')")
    monkeypatch.setattr('knowledge_wiki.read_wiki',lambda *a,**k:{'pages':[]})
    monkeypatch.setattr('risk_analysis.validated_risk_content',lambda *a:{})
    source=dict(kind='workflow',id='r',result={'nodes':[],'edges':[],'evidence':[dict(id='e',text='nothing',title='test')]})
    assert enrich_sources(db,[source])==[source]
    monkeypatch.setattr('risk_analysis.validated_risk_content',lambda *a:{'risks':[{'evidence_ids':['unadmitted']}]})
    assert enrich_sources(db,[source])==[source]
    db.close()


def test_current_wiki_keeps_full_source_query_and_page_identity(monkeypatch):
    page={'id':'medical:concept:abc','title':'의료 AI','claims':[{'text':'제한된 근거'}],'evidence':[dict(id='w',source_url='https://example.org/story?a=1&b=2',title='기사',text='의료 AI') ]}
    monkeypatch.setattr('knowledge_wiki.read_wiki',lambda *a,**k:{'pages':[page]})
    with sqlite3.connect(':memory:') as db:result=enrich_sources(db,[])
    assert result[0]['kind']=='wiki'
    assert result[0]['result']['evidence'][0]['source_url'].endswith('?a=1&b=2')
    assert any(n['type']=='SourceDocument' for n in result[0]['result']['nodes'])
    assert result[0]['result']['edges'][0]['relation']=='근거 인용'


def test_approved_risk_includes_conditions_and_counterevidence(monkeypatch):
    monkeypatch.setattr('knowledge_wiki.read_wiki',lambda *a,**k:{'pages':[]})
    r=dict(title='통제 위험',current_basis='관측',scenario='조건부 전개',assumptions=['성립 조건'],observed_indicators=['관찰 지표'],counter_evidence=['반대 증거'],escalation_signals=['악화 신호'],uncertainty='불확실',evidence_ids=['e'])
    monkeypatch.setattr('risk_analysis.validated_risk_content',lambda *a:{'risks':[r]})
    with sqlite3.connect(':memory:') as db:
        db.execute('create table strategic_workflow_artifacts(run_id,stage,payload_json)')
        db.execute("insert into strategic_workflow_artifacts values('r','final','{}')")
        result=enrich_sources(db,[dict(kind='workflow',id='r',result=dict(nodes=[],edges=[],evidence=[dict(id='e',title='통제',text='통제',url='https://example.org')]))])
    scenario=next(n for s in result for n in s['result']['nodes'] if n['type']=='ConditionalScenario')
    assert all(t in scenario['summary'] for t in ['성립 조건','반대 증거','악화 신호','불확실'])
