import copy

import risk_graph as graph


def fixture(monkeypatch,node_count=3):
    risks=[dict(id='r3',title='risk3',workflow_run_id='w',raw_evidence=[dict(url='https://example.org/a',title='Alpha')]),
           dict(id='r2',title='risk2',workflow_run_id='w',raw_evidence=[dict(url='https://example.org/b',title='BETA!')]),
           dict(id='r1',title='risk1',workflow_run_id='w',raw_evidence=[dict(url='https://example.org/a',title='Alpha')])]
    evidence=[dict(id='e1',source_url='https://example.org/a',title='Alpha'),
              dict(id='e2',source_url='https://example.org/other',title='beta'),
              dict(id='e3',source_url='https://example.org/unknown',title='Unknown')]
    nodes=[dict(id='g'+str(i),name='concept'+str(i),evidence_ids=[evidence[i%3]['id']]) for i in range(node_count)]
    payload=dict(nodes=nodes,edges=[],evidence=evidence)
    monkeypatch.setattr(graph,'read_risks',lambda *a,**kw:dict(risks=copy.deepcopy(risks)))
    monkeypatch.setattr('risk_views._apply_resolutions',lambda db,data:data)
    monkeypatch.setattr(graph,'priority_score',lambda r:dict(status='watch',range=[50,60]))
    monkeypatch.setattr(graph,'_selected_workflows',lambda db,run_ids:[copy.deepcopy(payload)])
    return risks


def test_shared_sources_and_title_matching_keep_exact_graph_links_and_order(monkeypatch):
    fixture(monkeypatch)
    result=graph.load_risk_graph(None)
    links=[(e['source'],e['target'],e['evidence_ids']) for e in result['edges'] if e['relation']=='GraphRAG 근거 연결']
    assert links==[('r3','graphrag:g0',['e1']),('r1','graphrag:g0',['e1']),('r2','graphrag:g1',['e2'])]
    similar=[e for e in result['edges'] if e['relation']=='공유 검토 근거 · 유사성']
    assert len(similar)==1 and (similar[0]['source'],similar[0]['target'])==('r3','r1') and similar[0]['similarity']==1
    assert not any(n['id']=='graphrag:g2' for n in result['nodes'])
    assert len([n for n in result['nodes'] if n['kind']=='evidence'])==2
    limited=graph.load_risk_graph(None,{'edge_limit':['5']})
    assert limited['edges']==result['edges'][:5]


def test_source_normalization_does_not_multiply_by_graph_node_count(monkeypatch):
    fixture(monkeypatch,300)
    original=graph._norm;calls=[]
    monkeypatch.setattr(graph,'_norm',lambda value:(calls.append(value) or original(value)))
    result=graph.load_risk_graph(None,{'edge_limit':['12000']})
    assert len(calls)<=10
    assert len([e for e in result['edges'] if e['relation']=='GraphRAG 근거 연결'])==300
