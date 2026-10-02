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
