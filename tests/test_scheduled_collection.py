"""Scheduler admission must preserve pauses, owners and current-input checks."""
from datetime import datetime, timezone
import json
import os
import sqlite3
from unittest.mock import patch

import pytest
from bulk_baseline import BulkBaselineService, freeze_item
from scheduled_collection import dispatch


@pytest.fixture
def db():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript('''
        CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE bulk_baseline_runs(id TEXT,status TEXT,owner_pid INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE bulk_baseline_cache(input_hash TEXT,result_json TEXT);
        CREATE TABLE bulk_baseline_documents(run_id TEXT,status TEXT);
        CREATE TABLE bulk_baseline_events(seq INTEGER PRIMARY KEY,run_id TEXT,stage TEXT,detail TEXT,created_at TEXT);
    ''')
    connection.executemany('INSERT INTO state VALUES (?,?)', [
        ('collector_last_success', '2026-09-28T12:00:00+00:00'),
        ('collector_corpus_snapshot', json.dumps({'status': 'complete'})),
    ])
    yield connection
    connection.close()


def run(db, **kwargs):
    kwargs.setdefault('check_models',lambda:{'ready':True})
    return dispatch(db, now=datetime(2026, 9, 28, 12, 0, 1, tzinfo=timezone.utc), **kwargs)


@pytest.mark.parametrize('status', ['paused', 'failed', 'requires_review', 'running', 'finishing'])
def test_unfinished_runs_are_not_bypassed(db, status):
    db.execute('INSERT INTO bulk_baseline_runs VALUES (?,?,?,?,?)', ('old', status, None, '2026-09-28', '2026-09-28T12:00:00+00:00'))
    assert run(db, request=lambda *_: pytest.fail('Must not dispatch'))['stage'] == 'baseline_attention_required'


def test_live_owner_is_adopted(db):
    db.execute('INSERT INTO bulk_baseline_runs VALUES (?,?,?,?,?)', ('live', 'running', os.getpid(), '2026-09-28', '2026-09-28T12:00:00+00:00'))
    assert run(db, request=lambda *_: pytest.fail('Duplicate run'))['stage'] == 'baseline_running'


def test_old_heartbeat_prevents_dispatch(db):
    db.execute("UPDATE state SET value='2026-09-28T11:00:00+00:00' WHERE key='collector_last_success'")
    assert run(db)['stage'] == 'waiting_for_collector'


def test_incomplete_extraction_prevents_dispatch(db):
    db.execute('UPDATE state SET value=? WHERE key=?', ('{"status":"extracting"}', 'collector_corpus_snapshot'))
    assert run(db)['stage'] == 'waiting_for_extraction'


def test_new_input_dispatches_but_current_verified_input_does_not(db):
    item = {'title': 'new', 'text': 'current input', 'source_url': 'https://example.org/new'}
    calls = []
    def request(path, payload):
        calls.append((path, payload))
        return {'run': {'id': 'new', 'status': 'running', 'metrics': {}}}
    with patch('scheduled_collection.all_corpus_items', return_value=[item]):
        assert run(db, request=request)['pending_current_inputs'] == 1
        assert len(calls) == 1
        snapshot = freeze_item(item)
        db.execute('INSERT INTO bulk_baseline_cache VALUES (?,?)', (snapshot['input_hash'], '{}'))
        # Matching hash alone is insufficient: rejected audit must not be reused.
        assert run(db, request=request)['stage'] == 'baseline_started'
        with patch.object(BulkBaselineService, '_valid_cached', return_value=True):
            assert run(db, request=request)['stage'] == 'up_to_date'
        assert len(calls) == 2


def engine_pause(db, code='timeout'):
    db.execute('INSERT INTO bulk_baseline_runs VALUES (?,?,?,?,?)',
               ('old', 'paused', None, '2026-09-28', '2026-09-28T12:00:00+00:00'))
    db.execute('INSERT INTO bulk_baseline_events VALUES (1,?,?,?,?)',
               ('old', 'engine_paused', json.dumps({'code': code}), '2026-09-28T11:59:40+00:00'))


def test_engine_recovery_resumes_same_run_and_persists_budget(db, tmp_path):
    engine_pause(db)
    calls = []
    def request(path, payload):
        calls.append(path)
        return {'run': {'id': 'old', 'metrics': {}}}
    options = dict(request=request, check_engine=lambda: True, recovery_path=tmp_path/'recovery.json')
    assert run(db, **options)['stage'] == 'baseline_resumed'
    assert calls == ['/api/baseline/old/resume']
    assert run(db, **options)['stage'] == 'recovery_backoff'
    assert json.loads(options['recovery_path'].read_text())['old']['attempts'] == 1


@pytest.mark.parametrize('code', ['rate_limit', 'authentication', 'permission', 'circuit_open'])
def test_recovery_excludes_restricted_failures(db, code, tmp_path):
    engine_pause(db, code)
    assert run(db, check_engine=lambda: pytest.fail('No probe'), recovery_path=tmp_path/'recovery.json')['stage'] == 'baseline_attention_required'


def test_user_pause_during_probe_blocks_resume(db, tmp_path):
    engine_pause(db)
    def probe():
        db.execute('INSERT INTO bulk_baseline_events VALUES (2,?,?,?,?)',
                   ('old', 'user_pause_requested', '{}', '2026-09-28T12:00:01+00:00'))
        return True
    assert run(db, check_engine=probe, request=lambda *_: pytest.fail('User paused'),
               recovery_path=tmp_path/'recovery.json')['stage'] == 'recovery_admission_changed'


def test_three_failed_probes_stop_until_progress(db, tmp_path):
    engine_pause(db)
    path = tmp_path/'recovery.json'
    for minute in (1, 11, 31):
        db.execute('UPDATE state SET value=? WHERE key=?',
                   (f'2026-09-28T12:{minute:02}:00+00:00','collector_last_success'))
        result = dispatch(db, now=datetime(2026,9,28,12,minute,tzinfo=timezone.utc),
                          check_engine=lambda: False, recovery_path=path)
        assert result['stage'] == 'engine_probe_failed'
    db.execute("UPDATE state SET value='2026-09-28T13:00:00+00:00' WHERE key='collector_last_success'")
    assert dispatch(db, now=datetime(2026,9,28,13,0,tzinfo=timezone.utc), recovery_path=path)['stage'] == 'recovery_limit'
