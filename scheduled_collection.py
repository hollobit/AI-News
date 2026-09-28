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


def dispatch(db, request=api, now=None):
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
    # Never bypass an unfinished run with fresh attempts, including engine pauses.
    if latest and latest['status'] != 'complete':
        return dict(summary, stage='baseline_attention_required', baseline_status=latest['status'])
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
            temporary = STATE.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            temporary.replace(STATE)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
