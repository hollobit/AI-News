from completion_throughput import AdaptiveAdmission


def test_pressure_reduces_only_admission_and_recovers_gradually():
    admission = AdaptiveAdmission(20, True)
    admission.observe('capacity', 100)
    assert admission.target == 10
    admission.observe('timeout', 110)
    assert admission.target == 10
    for _ in range(10): admission.observe(None, 120)
    assert admission.target == 10
    admission.observe(None, 131)
    assert admission.target == 11
    admission.observe('queue_timeout', 140)
    assert admission.target == 5
    for i in range(1000): admission.observe(None, 200+i)
    assert admission.target == 20


def test_fixed_mode_and_hard_failure_are_not_reinterpreted():
    fixed = AdaptiveAdmission(20)
    fixed.observe('capacity', 100)
    assert fixed.target == 20
    adaptive = AdaptiveAdmission(20, True)
    adaptive.observe('authentication', 100)
    assert adaptive.target == 20  # Existing engine gate must stop this failure.


def test_sustained_queue_pressure_without_throughput_gain_reduces_slowly():
    a=AdaptiveAdmission(20,True)
    sample=dict(calls=12,queued=10,wait_seconds=16,wait_ratio=.4,completed_per_minute=5)
    a.tune(sample,100);assert a.target==20
    a.tune(sample,130);assert a.target==20
    a.tune(dict(sample,completed_per_minute=7),160);assert a.target==20
    a.tune(dict(sample,completed_per_minute=7),220);assert a.target==19
    for _ in range(100):a.observe(None,230)
    assert a.target==19  # Success counting cannot immediately undo backpressure.
    for t in range(280,1480,60):a.tune(sample,t)
    assert a.target==10  # Queue tuning alone cannot drain down to one worker.
    a.observe('capacity',1500);assert a.target==5  # Errors retain stronger protection.


def test_pressure_recovery_requires_low_wait_and_real_completions():
    a=AdaptiveAdmission(20,True);a.observe('capacity',0)
    low=dict(calls=10,queued=0,wait_seconds=1,wait_ratio=.03,completed_per_minute=2)
    a.tune(low,60);assert a.target==10
    a.tune(low,120);assert a.target==11
    a.tune(dict(low,completed_per_minute=0),180);assert a.target==11
    a.tune(dict(low,calls=0),240);assert a.target==11
    fixed=AdaptiveAdmission(20);fixed.tune(low,120);assert fixed.target==20


def test_pressure_sample_is_owner_scoped_and_absence_is_not_success(tmp_path):
    from completion_throughput import pressure_sample
    from llm_runtime import LLMRuntime
    path=tmp_path/'runtime.db'
    assert pressure_sample(path) is None
    runtime=LLMRuntime(path)
    with runtime.db() as db:
        for owner,wait,status in [(1,20000,'complete'),(2,90000,'complete'),(1,0,'queued')]:
            db.execute('INSERT INTO llm_calls(role,status,owner_pid,queued_at,finished_at,wait_ms,run_ms,input_chars,schema_chars) VALUES (?,?,?,?,?,?,?,?,?)',('national',status,owner,900,990,wait,30000,1,1))
    s=pressure_sample(path,owner_pid=1,stamp=1000)
    assert s==dict(calls=1,queued=1,wait_seconds=20,wait_ratio=.4)


def test_missing_telemetry_does_not_disable_error_recovery_forever():
    a=AdaptiveAdmission(20,True);a.observe('capacity',0)
    a.tune(dict(calls=10,queued=0,wait_seconds=1,wait_ratio=.02,completed_per_minute=1),60)
    for _ in range(10):a.observe(None,300)
    assert a.target==11
