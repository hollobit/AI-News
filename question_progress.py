"""Persist real stage transitions; percentage counts completed work, not elapsed time."""
import json
import time
from statistics import median

STAGES = ('evidence', 'plan', 'synthesis', 'review', 'finalize')
LABELS = dict(evidence='검색 근거 준비', plan='질문 해석·검색 계획', synthesis='관계 분석·답변 작성',
              review='독립 근거 검토', finalize='최신 근거 확인·완료')
VERSION = 'workspace-progress-v1'


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS graph_question_progress(
        job_id TEXT PRIMARY KEY,profile TEXT NOT NULL,body TEXT NOT NULL,updated_at REAL NOT NULL)''')


def update(db, identity, stage, *, done=False, reset=False, **detail):
    row = db.execute('SELECT body FROM graph_question_progress WHERE job_id=?', (identity,)).fetchone()
    body = json.loads(row[0]) if row and not reset else {'stages': {}}
    now = time.time()
    entry = body['stages'].setdefault(stage, {'started_at': now})
    if done: entry.setdefault('completed_at', now)
    entry.update(detail)
    body['updated_at'] = now
    db.execute('INSERT OR REPLACE INTO graph_question_progress VALUES(?,?,?,?)', (identity, VERSION, json.dumps(body), now))


def view(db, identity, status, created_at):
    row = db.execute('SELECT body FROM graph_question_progress WHERE job_id=?', (identity,)).fetchone()
    if not row: return None
    body = json.loads(row[0]); now = time.time(); stages = body['stages']
    if status == 'complete':
        now = stages.get('finalize', {}).get('completed_at', body['updated_at'])
    complete = [s for s in STAGES if 'completed_at' in stages.get(s, {})]
    active = [s for s in STAGES if s in stages and s not in complete]
    result = dict(completed=len(complete), total=len(STAGES), percent=len(complete)*100//len(STAGES),
        elapsed_seconds=round(max(0, now-created_at)), active=active,
        stages=[dict(id=s, label=LABELS[s], status='complete' if s in complete else 'running' if s in active else 'pending',
                     seconds=round(max(0, stages[s].get('completed_at', now)-stages[s]['started_at']),1) if s in stages else None) for s in STAGES],
        remaining_seconds=None, estimate_samples=0, estimate_overdue=False)
    if status not in ('preparing', 'queued', 'running'): return result
    records = db.execute('''SELECT p.body FROM graph_question_progress p JOIN graph_question_jobs j ON j.id=p.job_id
        WHERE p.profile=? AND j.status='complete' AND p.job_id<>? ORDER BY p.updated_at DESC LIMIT 12''', (VERSION, identity)).fetchall()
    durations = {s: [] for s in STAGES}
    for record in records:
        for s, entry in json.loads(record[0])['stages'].items():
            if s in durations and entry.get('completed_at') and not entry.get('cached'):
                durations[s].append(max(0, entry['completed_at']-entry['started_at']))
    pending = [s for s in STAGES if s not in complete]
    if pending and all(durations[s] for s in pending):
        remaining = {s: max(0, median(durations[s]) - (now-stages[s]['started_at'] if s in stages else 0)) for s in pending}
        # Evidence preparation and question planning run concurrently.
        seconds = max(remaining.get('evidence', 0), remaining.get('plan', 0)) + sum(remaining.get(s, 0) for s in STAGES[2:])
        result.update(remaining_seconds=max(1, round(seconds)), estimate_samples=min(len(durations[s]) for s in pending),
                      estimate_overdue=any(s in active and now-stages[s]['started_at']>max(durations[s]) for s in pending))
    return result
