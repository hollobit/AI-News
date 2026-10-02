import sqlite3
from pipeline_health import record, view, acknowledge, snapshot


def report(codes, when='2026-09-30T01:00:00+00:00'):
    return dict(checked_at=when, stages=[], problems=[dict(code=c, message=c, action='확인') for c in codes])


def test_incident_lifecycle_and_notification_dedup(tmp_path):
    store = tmp_path/'health.sqlite'
    delivered = []
    def notify(message):
        delivered.append(message)
        return 'requested'
    record(report(['stale']), store, notify, 2000)
    record(report(['stale']), store, notify, 4000)
    assert len(delivered) == 1
    row = view(store)['incidents'][0]
    assert acknowledge(row['id'], store)
    assert view(store)['incidents'][0]['resolved_at'] is None
    record(report([]), store, notify, 5000)
    assert view(store)['incidents'][0]['resolved_at']
    record(report(['stale']), store, notify, 6000)
    assert len(delivered) == 2
    assert len(view(store)['incidents']) == 2


def test_failed_notification_retries_after_cooldown(tmp_path):
    store = tmp_path/'health.sqlite'
    calls = []
    def fail(message):
        calls.append(message)
        return 'failed'
    for stamp in [2000, 2100, 3000]:
        record(report(['error']), store, fail, stamp)
    assert len(calls) == 2
    assert view(store)['incidents'][0]['notification_status'] == 'failed'


def test_waiting_between_batches_and_old_complete_extraction_are_normal(tmp_path):
    import json
    path = tmp_path/'news.sqlite'
    when = '2026-09-30T01:00:00+00:00'
    from datetime import datetime
    stamp = datetime.fromisoformat(when).timestamp()
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE state(key,value);
        CREATE TABLE bulk_baseline_runs(id,status,owner_pid,updated_at,created_at);
        CREATE TABLE rsi_cycles(id,status,owner_pid,updated_at,created_at,error);
        CREATE TABLE bulk_baseline_documents(run_id,status);
        CREATE TABLE bulk_baseline_events(run_id,created_at,stage,detail);
        CREATE TABLE corpus_completion_documents(cycle_id,status);
        CREATE TABLE strategic_workflow_events(run_id,created_at);
        CREATE TABLE rsi_rounds(cycle_id,number,workflow_run_id);''')
        db.executemany('INSERT INTO state VALUES(?,?)', [('collector_last_success',when), ('collector_corpus_snapshot',json.dumps({'status':'complete','extracted_at':'2020-01-01'}))])
        db.execute('INSERT INTO bulk_baseline_runs VALUES(?,?,?,?,?)', ('b','complete',None,when,when))
        db.execute('INSERT INTO rsi_cycles VALUES(?,?,?,?,?,?)', ('d','waiting',None,when,when,None))
    for name in ['collection','risks']:
        (tmp_path/f'scheduled-{name}.json').write_text(json.dumps({'stage':'running','checked_at':when}))
    assert snapshot(path,tmp_path,stamp)['problems'] == []
    with sqlite3.connect(path) as db:
        db.execute("UPDATE rsi_cycles SET status='running'")
    problems = snapshot(path,tmp_path,stamp)['problems']
    assert [p['code'] for p in problems] == ['deep_orphan']
    with sqlite3.connect(path) as db:
        db.execute("UPDATE rsi_cycles SET status='waiting'")
        db.execute('ALTER TABLE rsi_rounds ADD COLUMN id TEXT')
        db.execute('ALTER TABLE rsi_rounds ADD COLUMN status TEXT')
        db.execute("INSERT INTO rsi_rounds VALUES('d',1,'workflow','round','planned')")
        db.execute('CREATE TABLE completion_engine_waits(round_id,next_attempt_at)')
        db.execute('INSERT INTO completion_engine_waits VALUES(?,?)', ('round', stamp+30))
    result = snapshot(path,tmp_path,stamp)
    assert [p['code'] for p in result['problems']] == ['deep_engine_retry']
    assert result['stages'][-1]['engine_retry_rounds'] == 1


def test_notification_arguments_are_data_and_timeout_is_reported(tmp_path):
    from pipeline_health import notify
    from unittest.mock import patch
    import subprocess
    with patch('pipeline_health.subprocess.run') as run:
        assert notify('quote " and newline\n are data') == 'requested'
        assert run.call_args.args[0][-1] == 'quote " and newline\n are data'
        run.side_effect = subprocess.TimeoutExpired('osascript', 15)
        assert notify('test') == 'failed'


def test_monitor_failure_does_not_resolve_unobserved_incidents(tmp_path):
    store = tmp_path/'health.sqlite'
    silent = lambda message: 'requested'
    record(report(['collector_stale']), store, silent, 2000)
    record(report(['monitor_error']), store, silent, 3000)
    assert all(r['resolved_at'] is None for r in view(store)['incidents'])
    record(report([]), store, silent, 4000)
    assert all(r['resolved_at'] for r in view(store)['incidents'])
