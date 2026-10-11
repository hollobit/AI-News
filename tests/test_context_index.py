import sqlite3
import pytest
from context_index import write, read, materialize
from graph_rag import build_retrieval
from graph_questions import quick_answer
from integrated_context import dossier


def source(kind, key, title, text):
    return dict(kind=kind, id=key, row={}, result=dict(
        nodes=[dict(id=key, name=title, type='PaperClaim' if kind=='paper' else 'SourceDocument', evidence_ids=[key])],
        edges=[], evidence=[dict(id=key, title=title, text=text, source_url='https://example.org/'+key)]))


def test_workspace_search_materializes_only_candidates_and_preserves_aliases(tmp_path, monkeypatch):
    sources=[source('paper','paper','Semiconductor methods','Semiconductor evaluation dataset'),
             source('baseline','news','반도체 평가','반도체 평가 실험 조건'),
             source('baseline','unrelated','Unrelated biology','Biology sampling protocol')]
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:(sources,{'completed_verified_papers':1}))
    path=tmp_path/'index.sqlite3'
    with sqlite3.connect(':memory:') as db:write(path,db,'current')
    graph=read(path,'current')
    with pytest.raises(ValueError,match='Obsolete'):read(path,'old')
    selected=materialize(graph,'반도체 평가')
    assert all(e['title']!='Unrelated biology' for e in selected.full_evidence)
    assert {e['title'] for e in selected.full_evidence}=={'Semiconductor methods','반도체 평가'}
    assert materialize(graph,'반도체 평가') is selected
    assert quick_answer(graph,'반도체 평가',[])['evidence']
    assert build_retrieval(graph,'Semiconductor methods')['evidence']
    view=dossier(graph,'반도체 평가')
    focus=view['groups']['paper'][0]['id']
    assert dossier(graph,'반도체 평가',focus)['focus']==focus
    for i in range(12):materialize(graph,'query '+str(i))
    assert len(graph.workspace_cache)==8


def test_workspace_short_korean_query_and_no_hits(tmp_path,monkeypatch):
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:([source('baseline','x','전력 수급','전력망의 용량')],{}))
    path=tmp_path/'index.sqlite3'
    with sqlite3.connect(':memory:') as db:write(path,db,'current')
    graph=read(path,'current')
    assert dossier(graph,'전력')['groups']['news']
    assert not any(dossier(graph,'무관한독특한질의')['groups'].values())


def test_workspace_snapshot_uses_source_index_instead_of_full_graph(tmp_path,monkeypatch):
    from app import connect
    from graph_snapshot import GraphSnapshots
    path=tmp_path/'news.sqlite3';connect(path).close()
    snapshots=GraphSnapshots(path)
    try:
        key,graph,future=snapshots.request({'workspace':['1']})
        graph=future.result(timeout=20)
        assert hasattr(graph,'workspace_store')
        assert not hasattr(graph,'prepared_index')
    finally:snapshots.close()
    reopened=GraphSnapshots(path)
    try:
        monkeypatch.setattr('subprocess.Popen',lambda *a,**k:pytest.fail('unchanged index rebuilt'))
        _,_,future=reopened.request({'workspace':['1']})
        assert future.result(timeout=5).search_key==key
    finally:reopened.close()


def test_published_wiki_edits_invalidate_workspace_but_not_unrelated_history():
    from context_index import wiki_revision
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE wiki_topics(id,published_revision);CREATE TABLE wiki_revisions(id,result_json);')
        db.execute("INSERT INTO wiki_topics VALUES('topic',1)")
        db.execute("INSERT INTO wiki_revisions VALUES(1,'original')")
        key=wiki_revision(db)
        db.execute("INSERT INTO wiki_revisions VALUES(2,'draft')")
        assert wiki_revision(db)==key
        db.execute("UPDATE wiki_revisions SET result_json='edited' WHERE id=1")
        assert wiki_revision(db)!=key
