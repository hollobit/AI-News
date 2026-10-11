from datetime import datetime, timezone
import json
import sqlite3
import threading
from unittest.mock import patch

import pytest

import forecast_progress as progress
from strategic_jobs import StrategicJobs
from test_forecasting import snapshot, report


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


@pytest.fixture
def db():
    with sqlite3.connect(':memory:') as conn:
        progress.setup(conn)
        conn.execute('CREATE TABLE intel_analysis_jobs(id,kind,status,snapshot_json,created_at,updated_at)')
        yield conn


def job(db, identity='new', status='running', start=300, end=300, kind='forecast'):
    value = dict(id=identity, status=status, created_at=iso(start), updated_at=iso(end))
    db.execute('INSERT INTO intel_analysis_jobs VALUES(?,?,?,?,?,?)',
               (identity, kind, status, json.dumps(snapshot()), value['created_at'], value['updated_at']))
    return value


def update(db, stamp, identity, stage=None, **kw):
    with patch('forecast_progress.time.time', return_value=stamp):
        progress.update(db, identity, stage, **kw)


def test_elapsed_time_cannot_finish_a_stage_and_terminal_time_freezes(db):
    current = job(db)
    update(db, 300, 'new', 'queue', done=True)
    update(db, 300, 'new', 'generation')
    with patch('forecast_progress.time.time', return_value=390):
        value = progress.view(db, current)
    assert value['percent'] == 25 and value['elapsed_seconds'] == 90
    assert value['remaining_seconds'] is None and value['estimate_samples'] == 0
    update(db, 400, 'new', status='failed')
    current['status'] = 'failed'
    with patch('forecast_progress.time.time', return_value=1000):
        value = progress.view(db, current)
    assert value['elapsed_seconds'] == 100 and value['percent'] == 25
    assert value['stages'][1]['status'] == 'stopped'


def test_historical_estimate_excludes_failed_other_kinds_and_versions(db):
    current = job(db)
    update(db, 300, 'new', 'queue', done=True)
    update(db, 300, 'new', 'generation')
    for i, duration in enumerate((60, 100, 80)):
        job(db, str(i), 'complete', 100, 100+duration)
    job(db, 'failure', 'failed', 100, 1000)
    job(db, 'different', 'complete', 100, 1000, kind='question')
    job(db, 'old_version', 'complete', 100, 1000)
    db.execute("UPDATE intel_analysis_jobs SET snapshot_json='{}' WHERE id='old_version'")
    with patch('forecast_progress.time.time', return_value=320):
        value = progress.view(db, current)
    assert value['remaining_seconds'] == 60 and value['estimate_samples'] == 3
    assert value['estimate_basis'] == 'total' and not value['estimate_overdue']
    with patch('forecast_progress.time.time', return_value=400):
        value = progress.view(db, current)
    assert value['estimate_overdue'] and value['percent'] == 25
    db.execute("DELETE FROM intel_analysis_jobs WHERE id='0'")
    assert progress.view(db, current)['remaining_seconds'] is None


def test_eta_prefers_stage_records_and_counts_queue_separately(db):
    for i in range(3):
        identity = str(i)
        job(db, identity, 'complete', 50, 191)
        for stage, begin, end in zip(progress.STAGES, [50,100,160,190], [100,160,190,191]):
            update(db, begin, identity, stage)
            update(db, end, identity, stage, done=True)
    current = job(db, status='queued')
    job(db, 'ahead', 'running', 250, 250, kind='question')
    job(db, 'ahead-queued', 'queued', 260, 260, kind='scenario_compare')
    update(db, 300, 'new', 'queue')
    value = progress.view(db, current)
    assert value['queue_ahead'] == 2 and value['remaining_seconds'] is None
    assert value['typical_seconds'] == 141
    update(db, 310, 'new', 'queue', done=True)
    update(db, 310, 'new', 'generation')
    current['status'] = 'running'
    with patch('forecast_progress.time.time', return_value=330):
        value = progress.view(db, current)
    assert value['remaining_seconds'] == 71 and value['estimate_basis'] == 'stages'
    assert value['estimate_samples'] == 3
    update(db, 350, 'new', 'generation', done=True)
    update(db, 350, 'new', 'review')
    with patch('forecast_progress.time.time', return_value=390):
        assert progress.view(db, current)['estimate_overdue']


def test_legacy_jobs_do_not_invent_stages_and_restart_keeps_prior_attempt(db):
    current = job(db, status='complete', start=100, end=200)
    value = progress.view(db, current)
    assert not value['recorded'] and value['percent'] is None and value['stages'] == []
    assert value['elapsed_seconds'] == 100
    update(db, 300, 'new', 'generation')
    update(db, 350, 'new', 'queue', restart=True)
    body = json.loads(db.execute('SELECT body FROM forecast_progress').fetchone()[0])
    assert set(body['stages']) == {'queue'}
    assert body['attempts'][0]['stages']['generation']['started_at'] == 300


@pytest.mark.parametrize('outcome', ['complete', 'needs_review', 'failed'])
def test_live_stages_publication_gate_and_no_additional_model_calls(tmp_path, outcome):
    entered = threading.Event()
    release = threading.Event()
    stages = []
    current = snapshot()['evidence']
    service = None
    def analyzer(prompt, schema, role):
        with service.db() as conn:
            row = conn.execute("SELECT id FROM intel_analysis_jobs WHERE status='running'").fetchone()
        value = service.get(row['id'])
        stages.append((role, value['progress']['percent'], [s['id'] for s in value['progress']['stages'] if s['status']=='running']))
        if role == 'strategic_forecast':
            entered.set()
            assert release.wait(3)
            return report()
        if outcome == 'failed':
            raise TimeoutError('private engine output must not be exposed')
        return dict(accepted=outcome=='complete', issues=[] if outcome=='complete' else ['추정 근거 부족'],
                    checked_evidence_ids=['a','b'], assumptions_separated=True, limitations=[])
    service = StrategicJobs(tmp_path/'jobs.db', analyzer=analyzer, enabled=True, current_evidence=lambda ids:current)
    try:
        value = service.start(snapshot())
        assert entered.wait(3)
        assert service.get(value['id'])['progress']['percent'] == 25
        release.set()
        service.futures[value['id']].result(timeout=3)
        final = service.get(value['id'])
        assert final['status'] == outcome
        assert final['progress']['percent'] == {'complete':100,'needs_review':75,'failed':50}[outcome]
        assert stages == [('strategic_forecast',25,['generation']),('strategic_forecast_verification',50,['review'])]
        assert final['progress']['remaining_seconds'] is None
        if outcome == 'complete':
            current[0]['version'] = 'changed'
            invalid = service.get(value['id'])
            assert invalid['status'] == 'needs_review' and not invalid['result']['verified']
            assert invalid['progress']['percent'] == 75
        if outcome == 'failed':
            assert 'private' not in final['error']
    finally:
        release.set()
        service.close()
