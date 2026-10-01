import json
import sqlite3
from copy import deepcopy
from unittest.mock import patch

from source_store import init,save
from source_review_queue import classify
from reach_pipeline import ReachPipeline
from completion_reconciliation import AdmissionChecks

URL='https://example.org/report'
RAW={'status':'fetched','title':'Report','text':'Original evidence','evidence_scope':'web_excerpt'}
STAMP='2026-10-01T00:00:00+00:00'


def test_first_fetch_indexes_without_full_analysis_and_changes_preserve_history(tmp_path):
    with sqlite3.connect(tmp_path/'db') as db:
        init(db)
        save(db,URL,RAW,STAMP)
        assert db.execute('select status from source_reanalysis').fetchone()[0]=='indexed'
        save(db,URL,RAW,STAMP)
        assert db.execute('select count(*) from source_review_history').fetchone()[0]==0
        save(db,URL,dict(RAW,text='Changed evidence'),STAMP)
        assert db.execute('select status from source_reanalysis').fetchone()[0]=='pending'
        assert db.execute('select status from source_review_history').fetchone()[0]=='indexed'


def test_corpus_delegation_is_not_a_verified_verdict(tmp_path):
    from corpus_completion import init as corpus_init
    with sqlite3.connect(tmp_path/'db') as db:
        db.row_factory=sqlite3.Row;init(db);corpus_init(db)
        save(db,URL,RAW,STAMP);save(db,URL,dict(RAW,text='Changed'),STAMP)
        db.execute('INSERT INTO corpus_completion_documents VALUES(?,?,?,?,?,0,NULL,?,?)',
            ('cycle','doc',0,json.dumps({'source_url':URL}),'pending','{}',STAMP))
        row=db.execute('select * from source_reanalysis').fetchone()
        assert classify(db,row)=='delegated_corpus'
        assert db.execute('select status from corpus_completion_documents').fetchone()[0]=='pending'


def test_blocked_engine_never_creates_workflow(tmp_path):
    pipeline=ReachPipeline(tmp_path/'db',autostart=False,reviewer=False)
    try:
        with pipeline.db() as db:
            save(db,URL,RAW,STAMP);save(db,URL,dict(RAW,text='Changed'),STAMP)
        with patch('source_review_queue.engine_blocked',return_value=True):pipeline._review()
        assert pipeline.workflow is None
        with pipeline.db() as db:
            assert db.execute('select status,run_id from source_reanalysis').fetchone()[:]==('blocked_engine','')
    finally:pipeline.close()


def test_pending_source_owned_by_corpus_does_not_call_model(tmp_path):
    from corpus_completion import init as corpus_init
    pipeline=ReachPipeline(tmp_path/'db',autostart=False,reviewer=False)
    class Workflow:
        enabled=True;active=None
        def create_run(self,*a):raise AssertionError('duplicate source analysis')
        def close(self):pass
    pipeline.workflow=Workflow()
    try:
        with pipeline.db() as db:
            corpus_init(db);save(db,URL,RAW,STAMP);save(db,URL,dict(RAW,text='Changed'),STAMP)
            db.execute('INSERT INTO corpus_completion_documents VALUES(?,?,?,?,?,0,NULL,?,?)',
                ('cycle','doc',0,json.dumps({'source_url':URL}),'pending','{}',STAMP))
        with patch('source_review_queue.engine_blocked',return_value=False):pipeline._review()
        with pipeline.db() as db:assert db.execute('select status from source_reanalysis').fetchone()[0]=='delegated_corpus'
    finally:pipeline.close()


def test_failed_source_waits_for_a_new_successful_probe(tmp_path):
    pipeline=ReachPipeline(tmp_path/'db',autostart=False,reviewer=False)
    class Workflow:
        enabled=True;active=None
        def get_run(self,id):return {'status':'failed','error':'[engine:timeout] timeout','updated_at':STAMP}
        def resume(self,*a):raise AssertionError('timer retry without recovery')
        def close(self):pass
    pipeline.workflow=Workflow()
    try:
        with pipeline.db() as db:
            save(db,URL,RAW,STAMP)
            db.execute("update source_reanalysis set status='running',run_id='prior'")
        with patch('source_review_queue.engine_blocked',return_value=False),patch('source_review_queue.recovered_after',return_value=False):
            pipeline._review();pipeline._review()
        with pipeline.db() as db:assert tuple(db.execute('select status,run_id from source_reanalysis').fetchone())==('blocked_engine','prior')
    finally:pipeline.close()


def test_admission_cache_invalidates_every_changed_dependency(tmp_path):
    with sqlite3.connect(tmp_path/'db') as db:
        checks=AdmissionChecks(db);row={'document_id':'doc'}
        stored={'id':'run','status':'complete','error':'','payload_json':'{"verified":true}'}
        item={'source_url':URL,'source_context':{'text':'body','status':'fetched'}}
        same,key=checks.unchanged('cycle',row,stored,item);assert not same
        checks.accept('cycle',row,key);checks.flush()
        assert checks.unchanged('cycle',row,stored,item)[0]
        for changed in [dict(stored,status='needs_review'),dict(stored,error='revoked'),dict(stored,payload_json='{}')]:
            assert not checks.unchanged('cycle',row,changed,item)[0]
        assert not checks.unchanged('cycle',row,stored,dict(item,source_context={'text':'new'}))[0]
        assert not checks.unchanged('other-cycle',row,stored,item)[0]
        checks.version='new-policy'
        assert not checks.unchanged('cycle',row,stored,item)[0]


def test_routing_explains_external_contract_without_relaxing_it():
    from workflow_routing import select
    value=select([{}],{'analysis_mode':'adaptive-v2'}, {'evidence':[{'origin':'external_source','text':'A tool released'}],'coverage':{}})
    assert value['path']=='multi-role' and value['reason']=='source_contract'
