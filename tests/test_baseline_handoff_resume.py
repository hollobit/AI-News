"""Extraction completion can lag the inputs already frozen by a baseline run."""
import json
import os
from unittest.mock import Mock, patch

import pytest

from bulk_baseline import freeze_item
from tests import test_bulk_baseline as fixtures
from tests.test_scheduled_collection import db, run


@pytest.fixture
def handoff_db(db):
    db.execute("ALTER TABLE bulk_baseline_runs ADD COLUMN error TEXT DEFAULT ''")
    db.execute('ALTER TABLE bulk_baseline_documents ADD COLUMN attempts INTEGER DEFAULT 0')
    db.execute('CREATE TABLE baseline_retry_budgets(run_id TEXT,document_id TEXT,attempt_limit INTEGER)')
    item={'title':'new','text':'current source','source_url':'https://example.org/new'}
    snapshot=freeze_item(item)
    db.execute('INSERT INTO bulk_baseline_runs VALUES (?,?,?,?,?,?)',
               ('old','paused',None,'2026-09-28','2026-09-28T12:00:00+00:00','new_input_handoff'))
    db.execute('INSERT INTO bulk_baseline_documents VALUES (?,?,?,?,?)',
               ('old','pending',snapshot['document_id'],snapshot['input_hash'],0))
    db.execute('INSERT INTO bulk_baseline_events VALUES (1,?,?,?,?)',
               ('old','input_handoff','{}','2026-09-28T12:00:00+00:00'))
    db.execute('UPDATE state SET value=? WHERE key=?',
               (json.dumps({'status':'complete','extracted_at':'new'}),'collector_corpus_snapshot'))
    with patch('scheduled_collection.all_corpus_items',return_value=[item]):yield db


def request():
    return Mock(return_value={'run':{'id':'old','status':'running','metrics':{}}})


def test_same_inputs_resume_same_run(handoff_db):
    call=request()
    result=run(handoff_db,request=call)
    assert result['stage']=='baseline_resumed' and result['automatic_handoff']
    assert result['new_or_changed_inputs']==0
    call.assert_called_once_with('/api/baseline/old/handoff',{'extracted_at':'new'})


@pytest.mark.parametrize('status,attempts',[('needs_review',1),('failed',1),('retry',2),('verified',1)])
def test_held_or_exhausted_documents_do_not_auto_retry(handoff_db,status,attempts):
    handoff_db.execute('UPDATE bulk_baseline_documents SET status=?,attempts=?',(status,attempts))
    assert run(handoff_db,request=lambda *_:pytest.fail('No automatic retry'))['stage']=='baseline_attention_required'


@pytest.mark.parametrize('change',['user_pause_requested','engine_paused','new_token','live_owner','new_run'])
def test_changed_admission_during_model_check_stops_resume(handoff_db,change):
    def check():
        if change in ('user_pause_requested','engine_paused'):
            handoff_db.execute('INSERT INTO bulk_baseline_events VALUES (2,?,?,?,?)',('old',change,'{}','2026-09-28T12:00:01+00:00'))
        elif change=='new_token':
            handoff_db.execute('UPDATE state SET value=? WHERE key=?',
                               ('{"status":"complete","extracted_at":"newer"}','collector_corpus_snapshot'))
        elif change=='live_owner':handoff_db.execute('UPDATE bulk_baseline_runs SET owner_pid=?',(os.getpid(),))
        else:handoff_db.execute('INSERT INTO bulk_baseline_runs VALUES (?,?,?,?,?,?)',('new','paused',None,'2026-09-29','2026-09-29',''))
        return {'ready':True}
    assert run(handoff_db,check_models=check,request=lambda *_:pytest.fail('Admission changed'))['stage']=='handoff_admission_changed'


def test_model_gate_still_applies(handoff_db):
    assert run(handoff_db,check_models=lambda:{'ready':False},request=lambda *_:pytest.fail('No access'))['stage']=='model_access_required'


def test_changed_inputs_create_carry_forward_run(handoff_db):
    handoff_db.execute("UPDATE bulk_baseline_documents SET input_hash='outdated'")
    call=request()
    assert run(handoff_db,request=call,coalesce=False)['stage']=='baseline_started'
    assert call.call_args.args[0]=='/api/baseline'
    assert call.call_args.args[1]['carry_forward_run']=='old'


@pytest.fixture
def service():
    case=fixtures.BaselineTests();case.setUp()
    service=case.service()
    original=case.done(service,service.start({'workers':1,'batch_size':1,'realtime_inputs':True})['id'])
    with service.db() as db:
        db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO state VALUES (?,?)',('collector_corpus_snapshot','{"status":"complete","extracted_at":"new"}'))
        db.execute("UPDATE bulk_baseline_runs SET status='paused',owner_pid=NULL,error='new_input_handoff'")
        db.execute("UPDATE bulk_baseline_documents SET status='retry',attempts=1 WHERE position=0")
        db.execute("UPDATE bulk_baseline_documents SET status='needs_review',attempts=2 WHERE position=1")
        db.execute("UPDATE bulk_baseline_documents SET status='failed',attempts=2 WHERE position=2")
        db.execute("INSERT INTO baseline_retry_budgets SELECT run_id,document_id,3 FROM bulk_baseline_documents WHERE position=0")
        db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES (?,?,?,?)',
                   (original['id'],'input_handoff','{}','2026-09-28T12:00:00+00:00'))
    service._launch=Mock()
    try:yield case,service,original['id']
    finally:case.tearDown()


def audit(service):
    with service.db() as db:
        return {table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in
                ('bulk_baseline_runs','bulk_baseline_documents','bulk_baseline_events','baseline_retry_budgets','baseline_generation_checkpoints')}


def test_worker_resume_advances_token_without_changing_attempts_or_review(service):
    _,worker,identity=service
    before=audit(worker)
    result=worker.resume(identity,handoff_token='new')
    after=audit(worker)
    assert result['id']==identity and result['settings']['extraction_token']=='new'
    worker._launch.assert_called_once_with(identity)
    for table in ('bulk_baseline_documents','baseline_retry_budgets','baseline_generation_checkpoints'):
        assert before[table]==after[table]
    assert all(r[2]!='manual_retry' for r in after['bulk_baseline_events'])
    assert json.loads(after['bulk_baseline_events'][-2][3])['automatic_handoff']


@pytest.mark.parametrize('change',['user_pause_requested','engine_paused','new_token','live_owner','changed_input','removed_input','requires_review','exhausted'])
def test_worker_checks_races_after_dispatch_without_mutation(service,change):
    case,worker,identity=service
    with worker.db() as db:
        if change in ('user_pause_requested','engine_paused'):
            db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES (?,?,?,?)',(identity,change,'{}','2026-09-28T12:00:01+00:00'))
        elif change=='new_token':db.execute('UPDATE state SET value=?',('{"status":"complete","extracted_at":"later"}',))
        elif change=='live_owner':db.execute('UPDATE bulk_baseline_runs SET owner_pid=?',(os.getpid(),))
        elif change=='requires_review':db.execute("UPDATE bulk_baseline_runs SET status='requires_review'")
        elif change=='exhausted':db.execute('UPDATE bulk_baseline_documents SET attempts=3')
    if change=='changed_input':case.items[0]['text']+=' changed'
    if change=='removed_input':case.items.pop()
    before=audit(worker)
    with pytest.raises(ValueError,match='입력 인계'):worker.resume(identity,handoff_token='new')
    assert audit(worker)==before
    worker._launch.assert_not_called()
