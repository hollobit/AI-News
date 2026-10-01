from graph_rag import GraphResult
from graph_retrieval import retrieve
from graph_disk_index import write,read
import pytest


def test_disk_retrieval_matches_memory_scores_diversity_and_scoped_edges(tmp_path):
    nodes=[dict(id='n'+str(i),name='한국 반도체' if i%2 else 'sovereign AI',type='Concept',summary='policy cost',aliases=[],evidence_ids=['e'+str(i)]) for i in range(35)]
    evidence=[dict(id='e'+str(i),title='한국 정책 반도체 비용' if i%2 else 'sovereign AI data infrastructure',text='한국 정책 cost semiconductor' if i%2 else 'sovereign AI sovereignty data',document_id='doc'+str(i),source_url='https://example.org/'+str(i),day='2026-09-01') for i in range(35)]
    edges=[dict(id='r'+str(i),source='n'+str(i),target='n'+str(i+1),evidence_ids=['e'+str(i),'e'+str(i+1)]) for i in range(34)]
    graph=GraphResult(nodes=nodes,edges=edges,evidence=evidence,coverage={})
    path=tmp_path/'index.sqlite';write(path,graph,'revision')
    disk=read(path,'revision')
    assert not disk.full_evidence
    for query,scope in [('한국 반도체',[]),('sovereign AI',[]),('cost',['n1']),('없는질문',[])]:
        expected=retrieve(graph,query,scope);actual=retrieve(disk,query,scope)
        for value in (expected,actual):value.pop('retrieval')
        assert expected==actual
    with pytest.raises(ValueError,match='Obsolete'):read(path,'changed')


def test_preview_only_hydrates_visible_nodes_and_actual_evidence(tmp_path):
    from graph_disk_index import preview
    nodes=[dict(id='n'+str(i),name=str(i),evidence_ids=['e'+str(i)]) for i in range(6)]
    evidence=[dict(id='e'+str(i),title=str(i),text='source '+str(i)) for i in range(6)]
    graph=GraphResult(nodes=nodes,edges=[],evidence=evidence,totals={'nodes':6,'edges':0},limits={})
    path=tmp_path/'view.sqlite';write(path,graph,'v')
    result=preview(read(path,'v'),{'max_nodes':['2'],'evidence_limit':['1']})
    assert [n['id'] for n in result['nodes']]==['n0','n1']
    assert [e['id'] for e in result['evidence']]==['e0'] and result['truncated']


def test_prune_retains_live_reader_generations(tmp_path):
    from graph_disk_index import Store,prune
    file=tmp_path/'old.sqlite3';file.write_bytes(b'cache')
    store=Store(file)
    prune(tmp_path,keep=0,budget=0)
    assert file.exists()
    store.release()
    prune(tmp_path,keep=0,budget=0)
    assert not file.exists()


def test_snapshot_prepares_in_child_and_reopens_without_rebuilding(tmp_path,monkeypatch):
    from app import connect
    from graph_snapshot import GraphSnapshots
    path=tmp_path/'news.sqlite'
    connect(path).close()
    service=GraphSnapshots(path)
    try:
        key,graph,future=service.request({})
        assert graph is None
        graph=future.result(timeout=20)
        assert hasattr(graph.prepared_index.nodes,'store')
        assert service.request({})[1] is graph
    finally:service.close()
    reopened=GraphSnapshots(path)
    try:
        def unexpected(*args,**kwargs):raise AssertionError('Unchanged index rebuilt')
        monkeypatch.setattr('subprocess.Popen',unexpected)
        _,_,future=reopened.request({})
        assert future.result(timeout=5).search_key==key
    finally:reopened.close()
