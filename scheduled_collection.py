"""Dispatch baseline analysis after successful Telegram extraction."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
from urllib.request import Request, urlopen

from bulk_baseline import BulkBaselineService, freeze_item
from improvement_selection import all_corpus_items
from recursive_improvement import owner_alive

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.runtime/scheduled-collection.json'
RECOVERY = ROOT / '.runtime/scheduled-collection-recovery.json'
RECOVERABLE = {'timeout', 'queue_timeout', 'database_locked', 'network', 'capacity'}


def probe():
    from app import load_local_env
    from semantic import run_structured
    load_local_env()
    try:
        result = run_structured('Return {"ok":true}. No tools or external facts.',
            {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
             'required': ['ok'], 'additionalProperties': False},
            role='engine_probe', timeout=15, queue_timeout=5, reasoning_effort='low')
        return result == {'ok': True}
    except Exception:
        return False


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def recover_baseline(db, latest, request, now, check_engine, recovery_path):
    if latest['status'] != 'paused' or owner_alive(latest['owner_pid']):
        return {'stage': 'baseline_attention_required'}
    event = db.execute("SELECT seq,detail,created_at FROM bulk_baseline_events WHERE run_id=? AND stage='engine_paused' ORDER BY seq DESC LIMIT 1",
                       (latest['id'],)).fetchone()
    if not event:
        return {'stage': 'baseline_attention_required'}
    stopped = db.execute("SELECT 1 FROM bulk_baseline_events WHERE run_id=? AND stage='user_pause_requested' AND seq>?",
                         (latest['id'], event['seq'])).fetchone()
    gap = (datetime.fromisoformat(latest['updated_at']) - datetime.fromisoformat(event['created_at'])).total_seconds()
    code = json.loads(event['detail']).get('code')
    if stopped or code not in RECOVERABLE or not 0 <= gap <= 300:
        return {'stage': 'baseline_attention_required'}
    records = json.loads(recovery_path.read_text()) if recovery_path.exists() else {}
    budget = records.setdefault(latest['id'], {'attempts': 0, 'stagnant': 0, 'verified': 0, 'next_attempt_at': 0})
    verified = db.execute("SELECT COUNT(*) FROM bulk_baseline_documents WHERE run_id=? AND status='verified'", (latest['id'],)).fetchone()[0]
    if verified > budget['verified']:
        budget.update(verified=verified, stagnant=0)
    if budget['attempts'] >= 20 or budget['stagnant'] >= 3:
        save_json(recovery_path, records)
        return {'stage': 'recovery_limit', 'recovery': budget}
    if now.timestamp() < budget['next_attempt_at']:
        return {'stage': 'recovery_backoff', 'recovery': budget}
    # Reserve the attempt before the model/API call so timeouts and restarts cannot
    # reset the budget. Verified progress resets only the no-progress counter.
    budget.update(attempts=budget['attempts'] + 1, stagnant=budget['stagnant'] + 1,
                  next_attempt_at=now.timestamp() + min(3600, 300 * 2 ** budget['stagnant']))
    save_json(recovery_path, records)
    if not check_engine():
        return {'stage': 'engine_probe_failed', 'recovery': budget}
    # Re-read admission immediately before mutation: a user may have paused it
    # during the probe. Same-run resume does not reset attempts on paused runs.
    states, current, active = inspect(db)
    user_stop = db.execute("SELECT 1 FROM bulk_baseline_events WHERE run_id=? AND stage='user_pause_requested' AND seq>?",
                           (latest['id'], event['seq'])).fetchone()
    if active or user_stop or current['id'] != latest['id'] or current['status'] != 'paused':
        return {'stage': 'recovery_admission_changed', 'recovery': budget}
    run = request('/api/baseline/' + latest['id'] + '/resume', {})['run']
    return {'stage': 'baseline_resumed', 'baseline_run': run['id'], 'metrics': run['metrics'], 'recovery': budget}


def api(path, payload):
    request = Request('http://127.0.0.1:8001' + path, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def inspect(db):
    states = dict(db.execute("SELECT key,value FROM state WHERE key IN (?,?)",
                            ('collector_last_success', 'collector_corpus_snapshot')))
    latest = db.execute('SELECT * FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    active = db.execute("SELECT id,owner_pid FROM bulk_baseline_runs WHERE status IN ('preparing','running','finishing')").fetchall()
    return states, dict(latest) if latest else None, any(owner_alive(r['owner_pid']) for r in active)


def dispatch(db, request=api, now=None, check_engine=probe, recovery_path=RECOVERY, check_models=None):
    now = now or datetime.now(timezone.utc)
    states, latest, active = inspect(db)
    extraction = json.loads(states.get('collector_corpus_snapshot', '{}'))
    summary = {'checked_at': now.isoformat(), 'last_telegram_check': states.get('collector_last_success'),
               'extraction': extraction, 'baseline_run': latest['id'] if latest else None}
    heartbeat = states.get('collector_last_success')
    if not heartbeat or (now - datetime.fromisoformat(heartbeat)).total_seconds() > 120:
        return dict(summary, stage='waiting_for_collector')
    if extraction.get('status') != 'complete':
        return dict(summary, stage='waiting_for_extraction')
    if active:
        return dict(summary, stage='baseline_running')
    # Never bypass an unfinished run with fresh attempts.
    if latest and latest['status'] != 'complete':
        return dict(summary, baseline_status=latest['status'],
                    **recover_baseline(db, latest, request, now, check_engine, recovery_path))
    snapshots = {s['document_id']: s for s in map(freeze_item, all_corpus_items(db))}
    pending = 0
    for snapshot in snapshots.values():
        cached = db.execute('SELECT result_json FROM bulk_baseline_cache WHERE input_hash=?',
                            (snapshot['input_hash'],)).fetchone()
        if not cached or not BulkBaselineService._valid_cached(json.loads(cached[0]), snapshot):
            pending += 1
    summary.update(total_unique=len(snapshots), pending_current_inputs=pending)
    if not pending:
        return dict(summary, stage='up_to_date')
    from model_access import ensure_model_access
    access=(check_models or ensure_model_access)()
    if not access['ready']:
        return dict(summary,stage='model_access_required',model_access=access)
    # After a timed-out POST, recheck the DB on the next tick rather than retrying.
    result = request('/api/baseline', {'workers': 2, 'batch_size': 1})['run']
    return dict(summary, stage='baseline_started', baseline_run=result['id'],
                baseline_status=result['status'], metrics=result['metrics'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true', help='Read the latest scheduler result')
    parser.add_argument('--check', action='store_true', help='Inspect the DB without starting analysis')
    args = parser.parse_args()
    if args.status:
        print(STATE.read_text() if STATE.exists() else '{}')
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (ROOT / '.runtime/scheduled-collection.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"stage":"already_checking"}')
            return
        with sqlite3.connect((ROOT / 'data/news.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=30) as db:
            db.row_factory = sqlite3.Row
            if args.check:
                states, latest, active = inspect(db)
                result = dict(stage='check_only', active_owner=active, latest_run=latest['id'] if latest else None,
                              latest_status=latest['status'] if latest else None, collector=states)
            else:
                try:
                    result = dispatch(db)
                except Exception as error:
                    # Exception text can include URLs or inputs; retain only the type.
                    result = dict(stage='dispatch_error', error_type=type(error).__name__,
                                  checked_at=datetime.now(timezone.utc).isoformat())
        if not args.check:
            save_json(STATE, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
