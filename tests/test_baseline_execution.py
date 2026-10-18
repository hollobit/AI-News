import json
from unittest.mock import Mock

import pytest

from baseline_execution import select_rows, key_for, read_draft, save_draft
from tests import test_bulk_baseline as fixtures


def row(identity, size=500, attempts=0):
    return dict(document_id=identity, attempts=attempts,
                snapshot_json=json.dumps({'evidence': [{'text': 'a'*size}]}))


def test_micro_batches_bound_full_context_and_isolate_retries():
    settings={'micro_batches':True,'batch_size':4}
    assert len(select_rows([row(str(i)) for i in range(8)],settings))==4
    assert len(select_rows([row('a',2000),row('b',2000),row('c',2000),row('d',2000)],settings))==3
    assert len(select_rows([row('long',3000),row('short')],settings))==1
    assert [r['document_id'] for r in select_rows([row('new'),row('retry',attempts=1)],settings)]==['new']
    assert len(select_rows([row('retry',attempts=1),row('retry2',attempts=1)],settings))==1


def test_review_interruption_reuses_unpublished_draft_without_regeneration():
    case=fixtures.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        review_calls=0
        def interrupted(prompt,schema):
            nonlocal review_calls
            if prompt.startswith('ROLE: baseline_verification'):
                review_calls+=1
                if review_calls==1:raise RuntimeError('[engine:network]')
            return case.model(prompt,schema)
        service=case.service(interrupted)
        run=case.done(service,service.start({'batch_size':1,'workers':1})['id'])
        assert run['status']=='paused' and run['metrics']['verified']==0
        with service.db() as db:
            assert db.execute('SELECT count(*) FROM bulk_baseline_cache').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM baseline_generation_checkpoints').fetchone()[0]==1
            assert db.execute('SELECT attempts FROM bulk_baseline_documents').fetchone()[0]==0
        done=case.done(service,service.resume(run['id'])['id'])
        assert done['status']=='complete'
        assert len([p for p in case.calls if p.startswith('ROLE: baseline_analysis')])==1
        assert review_calls==2
        with service.db() as db:
            events=[json.loads(r[0]) for r in db.execute("SELECT detail FROM bulk_baseline_events WHERE stage='analysis_and_verification'")]
            assert events[-1]['reused_drafts']==1 and events[-1]['generation_documents']==0
            assert db.execute('SELECT count(*) FROM baseline_generation_checkpoints').fetchone()[0]==0
    finally:case.tearDown()


def test_semantic_rejection_never_reuses_rejected_generation():
    case=fixtures.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        case.reject_ids={'https://example.com/0'}
        service=case.service();run=case.done(service,service.start({'workers':1,'batch_size':1})['id'])
        assert run['status']=='requires_review'
        assert len([p for p in case.calls if p.startswith('ROLE: baseline_analysis')])==2
        with service.db() as db:
            assert db.execute('SELECT count(*) FROM baseline_generation_checkpoints').fetchone()[0]==0
    finally:case.tearDown()


def test_checkpoint_policy_input_and_prompt_are_dependencies(monkeypatch):
    case=fixtures.BaselineTests();case.setUp()
    try:
        service=case.service();s={'document_id':'a','input_hash':'h'};p={}
        initial=key_for(service,s,p,None)
        assert key_for(service,dict(s,input_hash='changed'),p,None)!=initial
        assert key_for(service,s,p,{'issues':['rejected']})!=initial
        monkeypatch.setattr('model_policy.VERSION','new-policy')
        assert key_for(service,s,p,None)!=initial
        monkeypatch.setattr(service,'_prompt',lambda *args:'new prompt')
        assert key_for(service,s,p,None)!=initial
    finally:case.tearDown()


def test_jev_keyword_filter_can_only_remove_confident_fragments(tmp_path,monkeypatch):
    from baseline_decisions import filter_candidates
    monkeypatch.setattr('baseline_decisions.config',lambda:{'applies_baseline_keywords':True})
    answers={'keyword_0':{'choice':'fragment','confidence':.99,'probabilities':{'fragment':.99,'keep':.01}},
             'keyword_1':{'choice':'fragment','confidence':.80,'probabilities':{'fragment':.9,'keep':.1}}}
    caller=Mock(return_value={'status':'complete','answers':answers})
    monkeypatch.setattr('baseline_decisions.decide',caller)
    choices={'a':{'one':{'label':'잘린 구절','source_quote':'잘린 구절'},'two':{'label':'완전한 표현','source_quote':'완전한 표현'},'three':{'label':'엔비디아','source_quote':'엔비디아'}}}
    snapshots={'a':{'title':'source','evidence':[{'id':'e','text':'잘린 구절 전체 원문, 완전한 표현, 엔비디아'}]}}
    result,meta=filter_candidates(tmp_path/'db','run',snapshots,choices)
    assert set(result['a'])=={'two','three'} and len(choices['a'])==3
    assert meta['removed']==1 and caller.call_count==1
    caller.return_value={'status':'busy'}
    assert filter_candidates(tmp_path/'db','run',snapshots,choices)[0]==choices


def test_carry_keeps_rejection_attempts_and_frozen_denominator():
    case=fixtures.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        case.reject_ids={'https://example.com/0'}
        service=case.service();old=case.done(service,service.start({'workers':1,'batch_size':1})['id'])
        assert old['status']=='requires_review'
        before=len(case.calls)
        case.items.append(dict(case.items[0],source_url='https://example.com/new'))
        latest=case.done(service,service.start({'workers':1,'batch_size':1,'carry_forward_run':old['id']})['id'])
        assert latest['metrics']['verified']==1 and latest['metrics']['needs_review']==1
        assert service.get(old['id'])['metrics']['total']==1
        with service.db() as db:
            rows=db.execute("SELECT status,attempts FROM bulk_baseline_documents WHERE document_id='https://example.com/0' ORDER BY run_id").fetchall()
            assert [tuple(r) for r in rows]==[('needs_review',2),('needs_review',2)]
        assert len(case.calls)-before==2
        with pytest.raises(ValueError,match='새로 분석할 입력'):
            service.start({'carry_forward_run':latest['id']})
    finally:case.tearDown()


def test_handoff_waits_for_batch_and_user_pause_wins():
    import threading
    from bulk_baseline import now
    from baseline_handoff import can_carry
    case=fixtures.BaselineTests();case.setUp()
    try:
        entered=threading.Event();release=threading.Event()
        def blocked(prompt,schema):
            entered.set();release.wait(5);return case.model(prompt,schema)
        service=case.service(blocked)
        with service.db() as db:
            db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO state VALUES(?,?)',('collector_corpus_snapshot',json.dumps({'status':'complete','extracted_at':'old'})))
        run=service.start({'workers':1,'batch_size':1,'realtime_inputs':True})
        assert entered.wait(3)
        with service.db() as db:
            db.execute('UPDATE state SET value=?',(json.dumps({'status':'complete','extracted_at':'new'}),))
        # Durable explicit pause must beat a concurrent input wakeup.
        service.pause(run['id']);release.set()
        paused=case.done(service,run['id'])
        assert paused['status']=='paused'
        with service.db() as db:assert not can_carry(db,paused)
        with pytest.raises(ValueError,match='이전 실행'):
            service.start({'carry_forward_run':run['id']})
    finally:case.tearDown()


def test_resume_changes_only_performance_and_preserves_attempts():
    from baseline_execution import update_profile
    assert update_profile({'max_retries':1},{'workers':4,'micro_batches':True})=={'max_retries':1,'workers':4,'micro_batches':True}
    for bad in ({'max_retries':10},{'workers':20},{'micro_batches':'true'}):
        with pytest.raises(ValueError):update_profile({},bad)


def test_live_new_input_handoff_preserves_completed_batch_and_old_scope():
    import threading,time
    from baseline_handoff import can_carry
    case=fixtures.BaselineTests();case.setUp()
    try:
        entered=threading.Event();release=threading.Event()
        def blocked(prompt,schema):
            entered.set();release.wait(5);return case.model(prompt,schema)
        service=case.service(blocked)
        with service.db() as db:
            db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT INTO state VALUES(?,?)',('collector_corpus_snapshot',json.dumps({'status':'complete','extracted_at':'old'})))
        run=service.start({'workers':1,'batch_size':1,'realtime_inputs':True})
        assert entered.wait(3)
        with service.db() as db:
            db.execute('UPDATE state SET value=?',(json.dumps({'status':'complete','extracted_at':'new'}),))
        # Keep the in-flight call open until the cheap token check observes it.
        assert service.stop.wait(3)
        release.set();paused=case.done(service,run['id'])
        assert paused['error']=='new_input_handoff'
        assert paused['metrics']['total']==7 and paused['metrics']['verified']==1
        with service.db() as db:assert can_carry(db,paused)
    finally:release.set();case.tearDown()
