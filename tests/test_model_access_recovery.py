"""Access preflight and analysis must agree on recovery without bypassing budgets."""
import pytest

from engine_errors import EngineError
from llm_runtime import LLMRuntime
from llm_recovery import state, synchronize
from model_access import ensure_model_access


def fail(runtime, code='network'):
    for _ in range(3):
        with pytest.raises(EngineError):
            with runtime.slot('analysis',1,1) as ticket:
                ticket.error_code=code
                raise EngineError(code)
    synchronize(runtime)


def client(runtime, calls):
    def call(prompt,schema,**kwargs):
        calls.append(kwargs['role'])
        with runtime.slot(kwargs['role'],1,1):
            return {'ok':True}
    return call


def test_access_recovery_unlocks_normal_model_calls_and_checks_luna_once(tmp_path):
    runtime=LLMRuntime(tmp_path/'runtime.db');fail(runtime)
    calls=[]
    result=ensure_model_access(state_path=tmp_path/'access.json',
        runtime=runtime,caller=client(runtime,calls))
    assert result['ready'] and not state(runtime)['blocked']
    assert calls==['engine_probe','baseline_verification','risk_verification']
    with runtime.db() as db:
        assert db.execute("SELECT COUNT(*) FROM llm_recovery_events WHERE stage='probe_success'").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM llm_calls WHERE status='failed'").fetchone()[0]==3


@pytest.mark.parametrize('barrier',['budget','cooldown','authentication','lease'])
def test_automatic_preflight_never_bypasses_recovery_barriers(tmp_path,barrier):
    import time
    runtime=LLMRuntime(tmp_path/'runtime.db');fail(runtime,'authentication' if barrier=='authentication' else 'network')
    with runtime.db() as db:
        if barrier=='budget':db.execute('UPDATE llm_recovery SET attempts=3')
        if barrier=='cooldown':db.execute('UPDATE llm_recovery SET next_probe_at=?',(time.time()+100,))
        if barrier=='lease':db.execute('UPDATE llm_recovery SET owner_pid=999999999,lease_until=?',(time.time()+100,))
    before=state(runtime)
    for _ in range(2):
        result=ensure_model_access(state_path=tmp_path/'access.json',runtime=runtime,
            caller=lambda *a,**k:pytest.fail('No external calls while blocked'))
        assert not result['ready'] and result['stage']=='recovery_required'
        assert result['checks']==[]
    assert state(runtime)==before


def test_explicit_recovery_invalidates_cached_failure_and_retains_history(tmp_path):
    runtime=LLMRuntime(tmp_path/'runtime.db');fail(runtime)
    with runtime.db() as db:db.execute('UPDATE llm_recovery SET attempts=3')
    path=tmp_path/'access.json';calls=[]
    kwargs=dict(state_path=path,runtime=runtime,caller=client(runtime,calls))
    assert not ensure_model_access(**kwargs)['ready']
    result=ensure_model_access(force=True,**kwargs)
    assert result['ready'] and len(calls)==3
    assert ensure_model_access(**kwargs)==result
    assert len(calls)==3
    with runtime.db() as db:
        assert db.execute("SELECT attempts FROM llm_recovery_events WHERE stage='operator_probe'").fetchone()[0]==4
        assert db.execute("SELECT COUNT(*) FROM llm_calls WHERE status='failed'").fetchone()[0]==3
    fail(runtime)
    with runtime.db() as db:db.execute('UPDATE llm_recovery SET attempts=3')
    assert not ensure_model_access(**kwargs)['ready']  # Previously ready cache cannot conceal a new outage.
    assert len(calls)==3


def test_failed_access_probe_consumes_shared_budget_once(tmp_path):
    runtime=LLMRuntime(tmp_path/'runtime.db');fail(runtime)
    calls=[]
    def timeout(*a,**k):
        calls.append(1)
        raise EngineError('timeout')
    for _ in range(2):
        result=ensure_model_access(state_path=tmp_path/'access.json',runtime=runtime,caller=timeout)
        assert not result['ready']
    assert calls==[1] and state(runtime)['attempts']==1
    assert state(runtime)['error_code']=='timeout'
