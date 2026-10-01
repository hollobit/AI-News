import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from llm_runtime import LLMRuntime
from llm_recovery import shared_probe, synchronize, wait_until_ready, state
from engine_errors import EngineError


def outage(runtime, code='network'):
    for _ in range(3):
        with pytest.raises(EngineError):
            with runtime.slot('analysis',1,1) as ticket:
                ticket.error_code=code
                raise EngineError(code)


def test_single_probe_across_competing_runtime_instances(tmp_path):
    path=tmp_path/'runtime.db';r=LLMRuntime(path);outage(r)
    entered=threading.Event();release=threading.Event();calls=[]
    def probe():
        calls.append(1);entered.set();release.wait(3);return True
    synchronize(r)
    with ThreadPoolExecutor(2) as pool:
        leader=pool.submit(shared_probe,r,probe)
        assert entered.wait(2)
        assert not shared_probe(LLMRuntime(path),probe)
        release.set();assert leader.result()
    assert shared_probe(r,probe)
    assert len(calls)==1
    assert state(r)['blocked']==0


def test_hard_failure_does_not_trigger_automatic_probe(tmp_path):
    r=LLMRuntime(tmp_path/'runtime.db');outage(r,'authentication')
    with pytest.raises(EngineError,match='authentication'):
        wait_until_ready(r,lambda:pytest.fail('must not probe'),max_wait=0)


def test_failed_probe_persists_cooldown_and_budget(tmp_path):
    r=LLMRuntime(tmp_path/'runtime.db');outage(r);synchronize(r)
    assert not shared_probe(r,lambda:False)
    assert state(r)['attempts']==1
    assert not shared_probe(LLMRuntime(r.path),lambda:pytest.fail('cooldown'))
    with pytest.raises(EngineError,match='circuit_open'):
        wait_until_ready(r,lambda:False,max_wait=0)
    assert state(r)['attempts']==1


def test_probe_failure_diagnostics_and_explicit_operator_budget(tmp_path):
    r=LLMRuntime(tmp_path/'runtime.db');outage(r);synchronize(r)
    def denied():raise EngineError('authentication')
    assert not shared_probe(r,denied)
    assert state(r)['error_code']=='authentication'
    with r.db() as db:
        db.execute('UPDATE llm_recovery SET attempts=3,next_probe_at=0 WHERE id=1')
    assert not shared_probe(r,lambda:pytest.fail('automatic hard retry'))
    assert shared_probe(r,lambda:True,automatic=False)
    with r.db() as db:
        assert db.execute("SELECT COUNT(*) FROM llm_recovery_events WHERE stage='operator_probe' AND attempts=4").fetchone()[0]==1


def test_expired_leader_lease_can_be_claimed_but_live_lease_is_preserved(tmp_path):
    r=LLMRuntime(tmp_path/'runtime.db');state(r)
    with r.db() as db:
        db.execute('UPDATE llm_recovery SET blocked=1,owner_pid=999999999,lease_until=? WHERE id=1',(time.time()+10,))
    assert not shared_probe(r,lambda:pytest.fail('reserved lease'))
    with r.db() as db:db.execute('UPDATE llm_recovery SET lease_until=0 WHERE id=1')
    assert shared_probe(r,lambda:True)


def test_mixed_failures_never_hide_authentication_behind_network(tmp_path):
    r=LLMRuntime(tmp_path/'runtime.db')
    for code in ['authentication','network','network']:
        with pytest.raises(EngineError):
            with r.slot('analysis',1,1) as ticket:
                ticket.error_code=code;raise EngineError(code)
    with pytest.raises(EngineError,match='authentication'):
        wait_until_ready(r,lambda:pytest.fail('authentication must not auto retry'),max_wait=0)


def test_overdue_live_probe_is_not_duplicated(tmp_path):
    import os
    r=LLMRuntime(tmp_path/'runtime.db');state(r)
    with r.db() as db:
        db.execute('UPDATE llm_recovery SET blocked=1,owner_pid=?,lease_until=? WHERE id=1',(os.getpid(),time.time()-5))
    assert not shared_probe(r,lambda:pytest.fail('live leader still owns recovery'))
