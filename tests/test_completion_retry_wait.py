"""A live completion owner honors persisted cooldowns without a scheduler delay."""
import json
from unittest.mock import patch

import pytest

from corpus_completion import CompletionRunner,connect
from tests import test_corpus_completion as fixtures
from strategic_workflow import WorkflowService as ActualWorkflowService


@pytest.fixture
def case():
    fixture=fixtures.CompletionTests();fixture.setUp()
    fixture.items[:]=fixture.items[:1]
    try:yield fixture
    finally:fixture.tearDown()


def drive(case, *, failures=1, code='capacity', on_wait=None, **settings):
    runner=CompletionRunner(case.path,'cycle',workers=1,batch_size=1,**settings)
    original=runner.work;clock=[1000.];calls=[];waits=[]
    def work(service,planned):
        calls.append((clock[0],planned['id']))
        if len(calls)<=failures:return planned,{'status':'failed','error':'[engine:'+code+']'}
        return original(service,planned)
    def wait(delay):
        assert 0<delay<=1
        clock[0]+=delay;waits.append(delay)
        if on_wait:on_wait(runner,clock[0])
        return runner.stop.is_set()
    with patch('corpus_completion.time.time',lambda:clock[0]),patch('corpus_completion.time.monotonic',lambda:clock[0]),\
            patch.object(runner,'work',work),patch.object(runner.stop,'wait',wait):
        result=runner.run()
    return runner,result,calls,waits


@pytest.mark.parametrize('code',['capacity','timeout','queue_timeout','network','database_locked'])
def test_retries_at_due_time_on_same_round_without_new_attempt(case,code):
    runner,result,calls,waits=drive(case,code=code)
    assert result['status']=='complete'
    assert [t for t,_ in calls]==[1000.,1030.]
    assert len({r for _,r in calls})==1 and sum(waits)==30
    with connect(case.path) as db:
        assert db.execute('SELECT attempts FROM corpus_completion_documents').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM rsi_rounds').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM completion_engine_waits').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM completion_engine_events WHERE error!=''").fetchone()[0]==1


def test_backoff_increases_and_third_failure_stops(case):
    runner,result,calls,waits=drive(case,failures=3)
    assert result['status']=='paused' and runner.engine_error=='[engine:capacity]'
    assert [t for t,_ in calls]==[1000.,1030.,1090.]
    assert len({r for _,r in calls})==1
    with connect(case.path) as db:
        assert tuple(db.execute('SELECT failures,next_attempt_at FROM completion_engine_waits').fetchone())==(3,1210.)
        assert db.execute('SELECT attempts FROM corpus_completion_documents').fetchone()[0]==1
        assert db.execute('SELECT pause_requested FROM rsi_cycles').fetchone()[0]==0


@pytest.mark.parametrize('code',['authentication','permission','configuration','rate_limit','circuit_open'])
def test_restricted_failures_never_enter_retry_wait(case,code):
    _,result,calls,waits=drive(case,code=code)
    assert result['status']=='paused' and len(calls)==1 and waits==[]


def test_operator_pause_during_wait_is_preserved(case):
    def pause(runner,stamp):
        with connect(case.path) as db:db.execute('UPDATE rsi_cycles SET pause_requested=1')
    _,result,calls,waits=drive(case,on_wait=pause)
    assert result['status']=='paused' and len(calls)==1 and sum(waits)==1
    with connect(case.path) as db:
        assert db.execute('SELECT pause_requested FROM rsi_cycles').fetchone()[0]==1
        assert db.execute('SELECT next_attempt_at FROM completion_engine_waits').fetchone()[0]==1030.


def test_signal_stop_interrupts_wait(case):
    def stop(runner,stamp):runner.user_stopped=True;runner.stop.set()
    _,result,calls,waits=drive(case,on_wait=stop)
    assert result['status']=='paused' and len(calls)==1
    with connect(case.path) as db:assert db.execute('SELECT pause_requested FROM rsi_cycles').fetchone()[0]==1


@pytest.mark.parametrize('settings,expected_wait',[({'max_rounds':1},0),({'window_seconds':.25},.25)])
def test_explicit_run_limits_leave_checkpoint_for_next_owner(case,settings,expected_wait):
    _,result,calls,waits=drive(case,**settings)
    assert result['status']=='waiting' and len(calls)==1 and sum(waits)==expected_wait
    with connect(case.path) as db:
        assert db.execute('SELECT next_attempt_at FROM completion_engine_waits').fetchone()[0]==1030.
        assert db.execute('SELECT attempts FROM corpus_completion_documents').fetchone()[0]==1


def test_new_work_runs_during_existing_cooldown(case):
    refreshed=[]
    def add(runner,stamp):
        if refreshed:return
        from completion_inputs import reconcile
        item=dict(case.items[0],source_url='https://example.com/arrived')
        case.items.append(item)
        with connect(case.path) as db:reconcile(db,'cycle',case.items)
        refreshed.append(True)
    _,result,calls,waits=drive(case,on_wait=add)
    assert result['counts']=={'complete':2}
    assert [t for t,_ in calls]==[1000.,1001.,1030.]
    assert calls[0][1]==calls[2][1] and calls[0][1]!=calls[1][1]


def test_closed_or_claimed_rounds_do_not_keep_owner_waiting(case):
    runner=CompletionRunner(case.path,'cycle',workers=1,batch_size=1);runner.prepare()
    planned=runner.plan()
    with patch('corpus_completion.time.time',return_value=1000):runner.record(planned,{'status':'failed','error':'[engine:capacity]'})
    assert runner.next_retry_at()==1030.
    runner.claimed_rounds.add(planned['id']);assert runner.next_retry_at() is None
    runner.claimed_rounds.clear()
    with connect(case.path) as db:db.execute("UPDATE rsi_rounds SET status='complete'")
    assert runner.next_retry_at() is None


def test_real_workflow_resumes_failed_review_without_regenerating_draft(case):
    from tests.test_workflow_efficiency import Model,Sources
    from engine_errors import EngineError
    model=Model();attempted=[]
    case.items[0].update(title='AI 도구 공개',text='기업이 새 AI 개발 도구를 공개했다.',
                         source_context=Sources().fetch(case.items[0]['source_url']))
    def analyzer(prompt,schema):
        role=prompt.splitlines()[0][6:];attempted.append(role)
        if role=='integrated_verification' and attempted.count(role)==1:raise EngineError('capacity')
        return model(prompt,schema)
    def service(path,**kwargs):
        return ActualWorkflowService(path,sources=Sources(),analyzer=analyzer,
                                     extractor=lambda _:[],enabled=True,recover_interrupted=False)
    with patch('strategic_workflow.WorkflowService',service),patch('decision_tasks.workflow'):
        _,result,calls,waits=drive(case,failures=0)
    assert result['status']=='complete' and result['counts']=={'complete':1}
    assert len(calls)==2 and calls[0][1]==calls[1][1] and sum(waits)==30
    assert attempted==['integrated_analysis','integrated_verification','integrated_verification']
    with connect(case.path) as db:
        assert db.execute('SELECT COUNT(*) FROM strategic_workflow_runs').fetchone()[0]==1
        assert db.execute('SELECT attempts FROM corpus_completion_documents').fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM strategic_workflow_events WHERE stage='integrated_analysis' AND status='running'").fetchone()[0]==1
        final=json.loads(db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE stage='final'").fetchone()[0])
        assert final['verified'] and final['risk_verified']
