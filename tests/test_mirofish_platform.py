from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_exits_after_final_round_instead_of_entering_ipc_wait_mode():
    source = (ROOT / 'integrations/mirofish/upstream/backend/app/services/simulation_runner.py').read_text()
    assert 'cmd.append("--no-wait")' in source


def test_public_simulation_form_exposes_reddit_fallback_and_parallel_mode():
    source = (ROOT / 'static/simulation.js').read_text()
    source = ''.join(source.split()).replace('"', "'")
    assert "['reddit','Reddit(권장)']" in source
    assert "['twitter','Twitter']" in source
    assert "['parallel','Twitter+Reddit']" in source
