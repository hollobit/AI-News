import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from context_current import current,dependencies,load
from context_index import write
from tests import test_bulk_baseline as fixture


def test_source_bound_admission_accepts_unrelated_change_but_rejects_edit_and_withdrawal():
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:2]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with service.db() as db:
            wanted=db.execute('SELECT input_hash FROM bulk_baseline_cache ORDER BY document_id LIMIT 1').fetchone()[0]
        key=json.dumps(['baseline',wanted,None],ensure_ascii=False,separators=(',',':'))
        with patch('improvement_selection.all_corpus_items',side_effect=lambda db:case.items):
            with service.db() as db:expected=dependencies(load(db,[key]))
            assert expected and current(case.path,expected)
            case.items[1]['text']='unrelated changed input'
            assert current(case.path,expected)
            case.items[0]['text']='the cited input was edited'
            assert not current(case.path,expected)
            case.items[0]['text']='정부 소버린 AI 인프라 사업'
            assert current(case.path,expected)
            with service.db() as db:db.execute('DELETE FROM bulk_baseline_cache WHERE input_hash=?',(wanted,))
            assert not current(case.path,expected)
    finally:case.tearDown()


def test_current_source_passage_hash_must_match():
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with patch('improvement_selection.all_corpus_items',return_value=case.items):
            with service.db() as db:
                h=db.execute('SELECT input_hash FROM bulk_baseline_cache').fetchone()[0]
                expected=dependencies(load(db,[json.dumps(['baseline',h,None],separators=(',',':'))]))
                db.execute('CREATE TABLE source_health(url TEXT,hash TEXT,last_status TEXT)')
                db.execute("INSERT INTO source_health VALUES('https://example.org','original','fetched')")
            passages=[{'url':'https://example.org','hash':'original'}]
            assert current(case.path,expected,passages)
            with service.db() as db:db.execute("UPDATE source_health SET hash='new qualifier outside the short excerpt'")
            assert not current(case.path,expected,passages)
    finally:case.tearDown()


def test_consistent_workspace_requires_recent_current_policy_and_refreshes_records(tmp_path):
    from graph_snapshot import GraphSnapshots
    from context_index import read,materialize
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:1]
    snapshots=None
    try:
        service=case.service();case.done(service,service.start()['id'])
        snapshots=GraphSnapshots(case.path,directory=tmp_path/'indexes')
        file=snapshots.directory/'snapshot.sqlite3'
        with patch('improvement_selection.all_corpus_items',return_value=case.items):
            with service.db() as db:write(file,db,'old')
            with sqlite3.connect(file) as db:db.execute("UPDATE meta SET body='0' WHERE key='servable'")
            graph=snapshots.available_workspace()
            assert graph is not None
            selected=materialize(graph,'소버린 AI')
            assert selected.full_evidence and selected.workspace_dependencies
            case.items[0]['text']='source changed'
            selected=materialize(graph,'소버린 AI')
            assert not selected.full_evidence
            with sqlite3.connect(file) as db:db.execute("UPDATE meta SET body='0' WHERE key='admitted_at'")
            assert snapshots.available_workspace() is None
    finally:
        if snapshots:snapshots.close()
        case.tearDown()


def test_committed_corpus_cache_tracks_source_generation_and_policy(tmp_path):
    from context_current import corpus
    path=tmp_path/'generation.db'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE news(id); CREATE TABLE articles(id); CREATE TABLE projection_revisions(kind,value,identity); INSERT INTO projection_revisions VALUES('source',1,'epoch');")
    with patch('improvement_selection.all_corpus_items',side_effect=[[{'id':'original'}],[{'id':'changed'}],[{'id':'new-policy'}]]) as build:
        def read():
            with sqlite3.connect(path) as db:
                db.execute('BEGIN');return corpus(db)
        assert read()==[{'id':'original'}]
        assert read()==[{'id':'original'}] and build.call_count==1
        with sqlite3.connect(path) as db:db.execute("UPDATE projection_revisions SET value=2")
        assert read()==[{'id':'changed'}] and build.call_count==2
        with patch('verified_cache.policy',return_value='new-policy'):
            assert read()==[{'id':'new-policy'}] and build.call_count==3


def test_final_question_dependencies_exclude_unselected_candidates():
    from types import SimpleNamespace
    from graph_question_context import compose
    graph=SimpleNamespace(workspace_dependencies={'used':'hash1','other':'hash2'},
                          workspace_dependency_refs={'used':{'e1'},'other':{'e2'}})
    retrieval={'nodes':[],'edges':[],'evidence':[{'id':'e1','title':'관측 기사','text':'관측 사실','source_url':'https://example.org/one'}]}
    with patch('graph_rag.build_retrieval',return_value=retrieval):
        result=compose(graph,'관측 기사 의미',[],{'queries':['관측 기사 의미']})
    assert result['workspace_dependencies']=={'used':'hash1'}


def test_same_url_multiple_observations_have_stable_dependency_order():
    first={'kind':'workflow','id':'run','row':{},'result':{'nodes':[
        {'id':'same-url','summary':'Telegram title','evidence_ids':['telegram']},
        {'id':'same-url','summary':'Fetched title','evidence_ids':['url']}],'edges':[],'evidence':[]}}
    second=json.loads(json.dumps(first));second['result']['nodes'].reverse()
    assert dependencies([first])==dependencies([second])


def test_selected_baseline_lookup_reuses_only_same_committed_generation(tmp_path):
    from copy import deepcopy
    from context_current import corpus
    from bulk_baseline import freeze_item
    path=tmp_path/'selected.db'
    with sqlite3.connect(path) as db:
        db.executescript("PRAGMA journal_mode=WAL; CREATE TABLE news(id); CREATE TABLE articles(id); CREATE TABLE projection_revisions(kind,value,identity); INSERT INTO projection_revisions VALUES('source',1,'epoch');")
    items=[{'title':'관측 '+str(i),'text':'현재 원문','source_url':'https://example.org/'+str(i)} for i in range(3)]
    wanted=freeze_item(items[1])['input_hash']
    with patch('improvement_selection.all_corpus_items',side_effect=lambda db:deepcopy(items)) as read, \
         patch('bulk_baseline.freeze_item',wraps=freeze_item) as freeze:
        with sqlite3.connect(path) as old:
            old.execute('BEGIN')
            assert corpus(old,input_hashes={wanted})==[items[1]]
            assert corpus(old,input_hashes={wanted,'absent'})==[items[1]]
            assert freeze.call_count==3 and read.call_count==1
            # A concurrent source edit must not contaminate a pinned reader.
            original=deepcopy(items[1]);items[1]['text']='수정된 원문'
            with sqlite3.connect(path) as writer:writer.execute("UPDATE projection_revisions SET value=2")
            assert corpus(old,input_hashes={wanted})==[original]
        with sqlite3.connect(path) as fresh:
            fresh.execute('BEGIN')
            assert corpus(fresh,input_hashes={wanted})==[]
            updated=freeze_item(items[1])['input_hash']
            assert corpus(fresh,input_hashes={updated})==[items[1]]
            assert freeze.call_count==6 and read.call_count==2
            with patch('verified_cache.policy',return_value='changed-review-policy'):
                assert corpus(fresh,input_hashes={updated})==[items[1]]
                assert freeze.call_count==9 and read.call_count==3


def test_selected_baseline_lookup_is_not_reused_after_local_writes(tmp_path):
    from context_current import corpus
    from bulk_baseline import freeze_item
    path=tmp_path/'local-writes.db'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE news(id); CREATE TABLE articles(id); CREATE TABLE projection_revisions(kind,value,identity); INSERT INTO projection_revisions VALUES('source',1,'epoch');")
    item={'title':'원문','text':'수정 전'};wanted=freeze_item(item)['input_hash']
    with sqlite3.connect(path) as db, patch('improvement_selection.all_corpus_items',return_value=[item]) as read:
        db.execute('BEGIN')
        assert corpus(db,input_hashes={wanted})==[item]
        db.execute('INSERT INTO news VALUES(1)')
        item={'title':'원문','text':'아직 확정되지 않은 수정'};read.return_value=[item]
        # Fall back to full current items; baseline_sources still recomputes and
        # rejects the old hash. Uncommitted input must never enter shared cache.
        assert corpus(db,input_hashes={wanted})==[item]
        assert read.call_count==2


def test_cancelled_input_lookup_never_publishes_partial_generation(tmp_path):
    import threading
    import pytest
    from concurrent.futures import CancelledError
    from context_current import corpus
    from bulk_baseline import freeze_item
    from task_lifecycle import cancellation_scope
    path=tmp_path/'cancel.db'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE news(id); CREATE TABLE articles(id); CREATE TABLE projection_revisions(kind,value,identity); INSERT INTO projection_revisions VALUES('source',1,'epoch');")
    items=[{'title':str(i),'text':'원문'} for i in range(3)]
    wanted={freeze_item(item)['input_hash'] for item in items};stop=threading.Event()
    def interrupted(item):
        stop.set();return freeze_item(item)
    with sqlite3.connect(path) as db, patch('improvement_selection.all_corpus_items',return_value=items):
        db.execute('BEGIN')
        with patch('bulk_baseline.freeze_item',side_effect=interrupted),pytest.raises(CancelledError),cancellation_scope(stop):
            corpus(db,input_hashes=wanted)
        stop.clear()
        assert len(corpus(db,input_hashes=wanted))==3


def test_cached_input_lookup_does_not_cache_review_acceptance_or_withdrawal():
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with service.db() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS news(id); CREATE TABLE IF NOT EXISTS articles(id); CREATE TABLE IF NOT EXISTS projection_revisions(kind,value,identity); INSERT INTO projection_revisions VALUES('source',1,'epoch');")
            wanted,original=db.execute('SELECT input_hash,result_json FROM bulk_baseline_cache').fetchone()
        key=json.dumps(['baseline',wanted,None],separators=(',',':'))
        with patch('improvement_selection.all_corpus_items',return_value=case.items):
            with service.db() as db:
                db.execute('BEGIN');expected=dependencies(load(db,[key]))
            assert expected and current(case.path,expected)
            rejected=json.loads(original);rejected['verified']=False
            with service.db() as db:
                db.execute('UPDATE bulk_baseline_cache SET result_json=? WHERE input_hash=?',(json.dumps(rejected),wanted))
            assert not current(case.path,expected)
            with service.db() as db:
                db.execute('UPDATE bulk_baseline_cache SET result_json=? WHERE input_hash=?',(original,wanted))
            assert current(case.path,expected)
            with service.db() as db:db.execute('DELETE FROM bulk_baseline_cache WHERE input_hash=?',(wanted,))
            assert not current(case.path,expected)
    finally:case.tearDown()


def test_admission_timings_preserve_source_gate_and_contain_only_numbers():
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with service.db() as db:wanted=db.execute('SELECT input_hash FROM bulk_baseline_cache').fetchone()[0]
        key=json.dumps(['baseline',wanted,None],separators=(',',':'))
        with patch('improvement_selection.all_corpus_items',return_value=case.items):
            with service.db() as db:expected=dependencies(load(db,[key]))
            timing={}
            assert current(case.path,expected,(),timing)
            assert set(timing)=={'corpus_ms','analysis_sources_ms','selected_results_ms','dependencies_ms','passage_checks_ms','total_ms','thread_cpu_ms','validation_cache'}
            assert all(type(value) in (int,float) and value>=0 for key,value in timing.items() if key!='validation_cache')
            assert all(type(value) in (int,float) and value>=0 for value in timing['validation_cache'].values())
            case.items[0]['text']='edited original'
            timing={}
            assert not current(case.path,expected,(),timing)
            assert 'total_ms' in timing and 'passage_checks_ms' not in timing
    finally:case.tearDown()
