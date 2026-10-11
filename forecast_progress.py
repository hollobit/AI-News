"""Durable forecast stages and measured estimates; never infer completion from time."""
from datetime import datetime
import json
from statistics import median
import time

from forecasting import VERSION

PROFILE = VERSION + ':progress-v1'
STAGES = ('queue', 'generation', 'review', 'finalize')
LABELS = dict(queue='실행 대기', generation='조건부 전망 작성', review='독립 근거·확률 검토',
              finalize='검토 결과 확인·저장')
ACTIVE = ('queued', 'running')


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS forecast_progress (
        job_id TEXT PRIMARY KEY, profile TEXT NOT NULL, body TEXT NOT NULL, updated_at REAL NOT NULL)''')


def update(db, identity, stage=None, *, done=False, restart=False, status=None):
    row = db.execute('SELECT body FROM forecast_progress WHERE job_id=?', (identity,)).fetchone()
    body = json.loads(row[0]) if row else {'stages': {}}
    stamp = time.time()
    if restart:
        history = body.pop('attempts', [])
        body = {'stages': {}, 'attempts': history + [body], 'attempt_started_at': stamp}
    body.setdefault('attempt_started_at', stamp)
    if stage:
        entry = body['stages'].setdefault(stage, {'started_at': stamp})
        if done:
            entry.setdefault('completed_at', stamp)
    if status and status not in ACTIVE:
        body['finished_at'] = stamp
    body['updated_at'] = stamp
    db.execute('INSERT OR REPLACE INTO forecast_progress VALUES (?,?,?,?)',
               (identity, PROFILE, json.dumps(body), stamp))


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def view(db, job):
    row = db.execute('SELECT body FROM forecast_progress WHERE job_id=?', (job['id'],)).fetchone()
    body = json.loads(row[0]) if row else {}
    stages = body.get('stages', {})
    active = job['status'] in ACTIVE
    stamp = time.time() if active else body.get('finished_at', max(body.get('updated_at', 0), timestamp(job['updated_at'])))
    completed = {s for s in STAGES if 'completed_at' in stages.get(s, {})}
    # A once-complete result may now fail the source/hash/expiry gate in get().
    if job['status'] != 'complete':
        completed.discard('finalize')
    result = dict(recorded=bool(row), completed=len(completed) if row else None, total=len(STAGES),
                  percent=len(completed)*100//len(STAGES) if row else None,
                  elapsed_seconds=round(max(0, stamp-timestamp(job['created_at']))),
                  stages=[], remaining_seconds=None, estimate_samples=0, estimate_basis=None,
                  estimate_overdue=False, queue_ahead=0, typical_seconds=None)
    for s in STAGES if row else ():
        entry = stages.get(s)
        state = 'complete' if s in completed else 'running' if entry and active else 'stopped' if entry else 'pending'
        result['stages'].append(dict(id=s, label=LABELS[s], status=state,
            seconds=round(max(0, entry.get('completed_at', stamp)-entry['started_at']), 1) if entry else None))
    if not active:
        return result
    records = db.execute('''SELECT j.created_at,j.updated_at,p.body FROM intel_analysis_jobs j
        LEFT JOIN forecast_progress p ON p.job_id=j.id AND p.profile=?
        WHERE j.kind='forecast' AND j.status='complete' AND j.id<>?
          AND json_extract(j.snapshot_json,'$.forecast_version')=?
        ORDER BY j.updated_at DESC LIMIT 12''', (PROFILE, job['id'], VERSION)).fetchall()
    totals = [max(0, timestamp(r[1])-timestamp(r[0])) for r in records]
    result['estimate_samples'] = len(totals)
    if len(totals) >= 3:
        result['typical_seconds'] = max(1, round(median(totals)))
    if job['status'] == 'queued':
        result['queue_ahead'] = db.execute('''SELECT count(*) FROM intel_analysis_jobs
            WHERE id<>? AND (status='running' OR (status='queued' AND (created_at,id)<(?,?)))''',
            (job['id'], job['created_at'], job['id'])).fetchone()[0]
        return result
    pending = [s for s in STAGES[1:] if s not in completed]
    durations = {s: [] for s in pending}
    for record in records:
        for s, entry in json.loads(record[2] or '{}').get('stages', {}).items():
            if s in durations and 'completed_at' in entry:
                durations[s].append(max(0, entry['completed_at']-entry['started_at']))
    if pending and all(len(durations[s]) >= 3 for s in pending):
        elapsed = {s: max(0, stamp-stages[s]['started_at']) if s in stages else 0 for s in pending}
        remaining = sum(max(0, median(durations[s])-elapsed[s]) for s in pending)
        result.update(remaining_seconds=max(1, round(remaining)), estimate_basis='stages',
                      estimate_samples=min(len(durations[s]) for s in pending),
                      estimate_overdue=any(elapsed[s] >= median(durations[s]) for s in pending if s in stages))
    elif len(totals) >= 3:
        elapsed = max(0, stamp-body.get('attempt_started_at', timestamp(job['created_at'])))
        result.update(remaining_seconds=max(1, round(median(totals)-elapsed)), estimate_basis='total',
                      estimate_overdue=elapsed >= median(totals))
    return result
