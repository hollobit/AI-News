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


def test_incremental_index_revalidates_changes_removals_and_matches_full_build(tmp_path,monkeypatch):
    import json
    from context_index import previous_index
    sources=[source('baseline','stable','반도체 평가','동일 원문'),
             source('paper','changed','반도체 방법','폐기된 실험 조건'),
             source('baseline','withdrawn','반도체 결과','철회된 근거')]
    checked=[]
    def admitted(db):checked.append(1);return sources,{'sources':len(sources)}
    monkeypatch.setattr('graph_rag._analysis_sources',admitted)
    old=tmp_path/'old.sqlite3'
    with sqlite3.connect(':memory:') as db:assert write(old,db,'old')['mode']=='full'
    assert previous_index(tmp_path)==old
    sources[1]=source('paper','changed','반도체 방법','현재 검토된 조건')
    sources.pop();sources.append(source('baseline','added','반도체 신규','새 관측'))
    new=tmp_path/'new.sqlite3';full=tmp_path/'full.sqlite3'
    with sqlite3.connect(':memory:') as db:
        assert write(new,db,'new',old)==dict(mode='incremental',sources=3,changed=2,removed=1)
        write(full,db,'new')
    assert len(checked)==3
    for query in ('반도체','폐기된','철회된','현재','새 관측'):
        actual=materialize(read(new,'new'),query)
        expected=materialize(read(full,'new'),query)
        assert actual.full_evidence==expected.full_evidence
    # The earlier published file and its readers remain untouched.
    with sqlite3.connect(old) as db:
        assert json.loads(db.execute("SELECT body FROM meta WHERE key='build'").fetchone()[0])['mode']=='full'
        assert db.execute("SELECT 1 FROM sources WHERE text LIKE '%철회된%'").fetchone()
    unchanged=tmp_path/'unchanged.sqlite3'
    with sqlite3.connect(':memory:') as db:
        assert write(unchanged,db,'newer',new)==dict(mode='incremental',sources=3,changed=0,removed=0)


def test_incremental_research_preserves_different_runs_for_same_document(tmp_path,monkeypatch):
    sources=[dict(source('research','same','반도체','첫 관측'),row={'run_id':'first'}),
             dict(source('research','same','반도체','다른 관측'),row={'run_id':'second'})]
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:(sources,{}))
    old=tmp_path/'old.sqlite3';new=tmp_path/'new.sqlite3'
    with sqlite3.connect(':memory:') as db:
        write(old,db,'old')
        sources.pop(0)
        assert write(new,db,'new',old)['removed']==1
    with sqlite3.connect(new) as db:
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0]==1
        assert '다른 관측' in db.execute('SELECT text FROM sources').fetchone()[0]


def test_graph_set_order_does_not_reindex_identical_sources(tmp_path,monkeypatch):
    entry=source('workflow','run','반도체','실험 조건')
    entry['result']['nodes'] += [dict(id='other',name='두번째',evidence_ids=['run'])]
    entry['result']['edges'] = [dict(source='run',target='other'),dict(source='other',target='run')]
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:([entry],{}))
    old=tmp_path/'old.sqlite3';new=tmp_path/'new.sqlite3'
    with sqlite3.connect(':memory:') as db:
        write(old,db,'old')
        entry['result']['nodes'].reverse();entry['result']['edges'].reverse()
        assert write(new,db,'new',old)['changed']==0


def test_obsolete_cold_workspace_is_retained_only_as_refresh_base(tmp_path,monkeypatch):
    from app import connect
    from graph_snapshot import GraphSnapshots
    from context_index import previous_index
    path=tmp_path/'news.sqlite3';connect(path).close()
    snapshots=GraphSnapshots(path)
    params={'workspace':['1']};key=snapshots.key(params)
    calls=[]
    def current(params):
        calls.append(1)
        return key if len(calls)==1 else 'new-revision'
    monkeypatch.setattr(snapshots,'key',current)
    try:
        with pytest.raises(RuntimeError,match='갱신'):
            snapshots._prepare_disk(key,params)
        assert not snapshots.ready
        assert previous_index(snapshots.directory).name==key+'.sqlite3'
    finally:snapshots.close()


def test_obsolete_base_cannot_be_read_even_with_its_old_key(tmp_path,monkeypatch):
    from context_index import previous_index
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:([source('baseline','x','제목','원문')],{}))
    old=tmp_path/'old.sqlite3';new=tmp_path/'new.sqlite3'
    with sqlite3.connect(':memory:') as db:write(old,db,'old')
    with sqlite3.connect(old) as db:db.execute("UPDATE meta SET body='0' WHERE key='servable'")
    with pytest.raises(ValueError,match='Obsolete'):read(old,'old')
    assert previous_index(tmp_path)==old
    with sqlite3.connect(':memory:') as db:write(new,db,'new',old)
    assert read(new,'new').search_key=='new'
