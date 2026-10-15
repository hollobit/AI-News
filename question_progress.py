"""Persist real stage transitions; percentage counts completed work, not elapsed time."""
import json
import time
from statistics import median

STAGES = ('evidence', 'plan', 'synthesis', 'review', 'finalize')
LABELS = dict(evidence='검색 근거 준비', plan='질문 해석·검색 계획', synthesis='관계 분석·답변 작성',
              review='근거·설명 충실도 검토', finalize='최신 근거 확인·완료')
REVIEW_LABELS = dict(repair='부족한 설명 보완', recheck='보완 답변 독립 재검토', incomplete='설명 검토 미완료')
PLAN_LABELS = dict(jev='Jev 질문 유형·계획 적합성 판단', jev_template='Jev 검색 계획 선택',
                   sol='Sol 질문 해석·검색 계획', cache='저장 검색 계획 재사용', deterministic='단일 주제 검색 계획')
PLAN_REASONS = dict(uncertain='Jev 판단이 불확실해 Sol이 검색 계획을 작성합니다.',
    unavailable='Jev 연결이 지연되어 Sol이 검색 계획을 작성합니다.',
    rate_limit='Jev 호출 한도로 Sol이 검색 계획을 작성합니다.',
    cooldown='Jev 재시도 대기 중으로 Sol이 검색 계획을 작성합니다.',
    busy='다른 Jev 작업이 진행 중이어서 Sol이 검색 계획을 작성합니다.',
    invalid_response='Jev 응답을 확인할 수 없어 Sol이 검색 계획을 작성합니다.',
    authentication='Jev 인증을 확인할 수 없어 Sol이 검색 계획을 작성합니다.',
    provider_error='Jev 제공자 오류로 Sol이 검색 계획을 작성합니다.',
    storage_unavailable='Jev 기록 저장 지연으로 Sol이 검색 계획을 작성합니다.',
    unsupported_question='맞춤 해석이 필요한 질문은 Sol이 검색 계획을 작성합니다.',
    template_selected='질문 유형·계획 적합성을 확인했습니다. 답변 작성과 독립 검토는 이어서 진행합니다.')
VERSION = 'workspace-progress-v2'


def setup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS graph_question_progress(
        job_id TEXT PRIMARY KEY,profile TEXT NOT NULL,body TEXT NOT NULL,updated_at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS graph_question_events(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,stage TEXT NOT NULL,
        status TEXT NOT NULL,phase TEXT NOT NULL,created_at REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS question_events_job ON graph_question_events(job_id,seq)')


def update(db, identity, stage, *, done=False, reset=False, **detail):
    row = db.execute('SELECT body FROM graph_question_progress WHERE job_id=?', (identity,)).fetchone()
    body = json.loads(row[0]) if row and not reset else {'stages': {}}
    now = time.time()
    entry = body['stages'].setdefault(stage, {'started_at': now})
    if done: entry.setdefault('completed_at', now)
    entry.update(detail)
    body['updated_at'] = now
    db.execute('INSERT OR REPLACE INTO graph_question_progress VALUES(?,?,?,?)', (identity, VERSION, json.dumps(body), now))
    status='complete' if done else 'running';phase=str(detail.get('phase',''))
    previous=db.execute('SELECT stage,status,phase FROM graph_question_events WHERE job_id=? ORDER BY seq DESC LIMIT 1',(identity,)).fetchone()
    if reset or not previous or tuple(previous)!=(stage,status,phase):
        db.execute('INSERT INTO graph_question_events(job_id,stage,status,phase,created_at) VALUES(?,?,?,?,?)',(identity,stage,status,phase,now))


def view(db, identity, status, created_at):
    row = db.execute('SELECT body FROM graph_question_progress WHERE job_id=?', (identity,)).fetchone()
    if not row: return None
    body = json.loads(row[0]); now = time.time(); stages = body['stages']
    if status not in ('preparing', 'queued', 'running'):
        now = stages.get('finalize', {}).get('completed_at', body['updated_at'])
    complete = [s for s in STAGES if 'completed_at' in stages.get(s, {})]
    active = [s for s in STAGES if s in stages and s not in complete]
    result = dict(completed=len(complete), total=len(STAGES), percent=len(complete)*100//len(STAGES),
        elapsed_seconds=round(max(0, now-created_at)), active=active,
        stages=[dict(id=s, label=(REVIEW_LABELS if s=='review' else PLAN_LABELS if s=='plan' else {}).get(stages.get(s,{}).get('phase'),LABELS[s]), status='complete' if s in complete else 'running' if s in active else 'pending',
                     seconds=round(max(0, stages[s].get('completed_at', now)-stages[s]['started_at']),1) if s in stages else None) for s in STAGES],
        remaining_seconds=None, estimate_samples=0, estimate_overdue=False)
    route=stages.get('plan',{}).get('routing')
    if route:
        result['planning']=route
        result['planning_note']=PLAN_REASONS.get(route.get('reason'),'')
    if status not in ('preparing', 'queued', 'running'): return result
    records = db.execute('''SELECT p.body FROM graph_question_progress p JOIN graph_question_jobs j ON j.id=p.job_id
        WHERE p.profile=? AND j.status='complete' AND p.job_id<>? ORDER BY p.updated_at DESC LIMIT 12''', (VERSION, identity)).fetchall()
    durations = {s: [] for s in STAGES}
    for record in records:
        for s, entry in json.loads(record[0])['stages'].items():
            # Do not estimate the new Jev route from old Sol planning durations.
            same_route=s!='plan' or entry.get('phase','')==stages.get(s,{}).get('phase','')
            if s in durations and same_route and entry.get('completed_at') and not entry.get('cached'):
                durations[s].append(max(0, entry['completed_at']-entry['started_at']))
    pending = [s for s in STAGES if s not in complete]
    if pending and all(durations[s] for s in pending):
        remaining = {s: max(0, median(durations[s]) - (now-stages[s]['started_at'] if s in stages else 0)) for s in pending}
        # Evidence preparation and question planning run concurrently.
        seconds = max(remaining.get('evidence', 0), remaining.get('plan', 0)) + sum(remaining.get(s, 0) for s in STAGES[2:])
        result.update(remaining_seconds=max(1, round(seconds)), estimate_samples=min(len(durations[s]) for s in pending),
                      estimate_overdue=any(s in active and now-stages[s]['started_at']>max(durations[s]) for s in pending))
    return result
