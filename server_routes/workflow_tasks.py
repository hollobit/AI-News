"""Bounded task timelines derived from persisted events, without loading reports."""
from operations_health import age

PATHS = {
    'compact-v1': ['integrated_analysis', 'integrated_verification'],
    'parallel-drafts-v1': ['strategy_draft', 'risk_assessment', 'integrated_verification'],
    'multi-role': ['morphology', 'graph_retrieval', 'national', 'technology',
                   'risk_assessment', 'deliberation', 'synthesis', 'risk_verification', 'verification'],
}
PREFIX = ['collection', 'enrichment', 'execution_plan']
EXTRAS = ['source_repair', 'repair_morphology', 'revision', 'reverification',
          'risk_revision', 'risk_reverification', 'integrated_reverification', 'strategy_audit_reuse']


def task_detail(db, task, tables, stamp, owner_alive):
    run_id = task['workflow_run_id']
    route = None
    task['call_ids'] = []
    task['reused_stages'] = 0
    if 'strategic_workflow_artifacts' in tables:
        row = db.execute("SELECT json_extract(payload_json,'$.path') FROM strategic_workflow_artifacts WHERE run_id=? AND stage='execution_plan'", (run_id,)).fetchone()
        route = row[0] if row else None
        timing=db.execute("SELECT json_extract(payload_json,'$.result.retrieval') FROM strategic_workflow_artifacts WHERE run_id=? AND stage='graph_retrieval'", (run_id,)).fetchone()
        if timing and timing[0]:
            import json
            metrics=json.loads(timing[0])
            task['retrieval_timing']={key:metrics[key] for key in ('source_lookup_ms','total_ms','validated_workflows') if key in metrics}
        task['call_ids'] = [r[0] for r in db.execute("SELECT json_extract(payload_json,'$.call_id') FROM strategic_workflow_artifacts WHERE run_id=? AND stage LIKE 'model_call_%' LIMIT 100", (run_id,)) if isinstance(r[0], int)]
        task['reused_stages'] = db.execute("SELECT count(*) FROM strategic_workflow_artifacts WHERE run_id=? AND stage LIKE 'reuse_%'", (run_id,)).fetchone()[0]
    events = [dict(r) for r in db.execute('''SELECT stage,status,created_at FROM strategic_workflow_events
        WHERE run_id=? ORDER BY seq DESC LIMIT 200''', (run_id,))][::-1]
    allowed = set(PREFIX + EXTRAS + ['final'] + sum(PATHS.values(), []))
    latest = {}; starts = {}; attempts = {}
    for event in events:
        key = event['stage']
        if key not in allowed:
            continue
        if event['status'] == 'running':
            starts[key] = event['created_at']; attempts[key] = attempts.get(key, 0) + 1
        latest[key] = event
    expected = PREFIX + PATHS.get(route, [])
    expected += [key for key in latest if key in EXTRAS or key not in expected and key != 'final']
    expected += ['final']
    wait = None
    if 'completion_engine_waits' in tables:
        row = db.execute('SELECT failures,next_attempt_at FROM completion_engine_waits WHERE round_id=?', (task['id'],)).fetchone()
        if row: wait = dict(row)
    steps = []
    for key in expected:
        event = latest.get(key, {})
        status = event.get('status', 'pending')
        start = starts.get(key)
        end = event.get('created_at') if status != 'running' else None
        elapsed = max(0, round(age(start, stamp) - (age(end, stamp) if end else 0))) if start else None
        display = status
        if status == 'running' and task['status'] not in ('running', 'planned'):
            display = 'interrupted'
        elif status == 'running' and not owner_alive:
            display = 'owner_missing'
        elif status == 'running' and wait and wait['next_attempt_at'] > stamp:
            display = 'retry'
        steps.append(dict(stage=key, status=display, recorded_status=status, updated_at=event.get('created_at'),
                          started_at=start, elapsed_seconds=elapsed, attempts=attempts.get(key, 0)))
    complete = sum(s['recorded_status'] == 'complete' for s in steps)
    task.update(steps=steps, route=route, retry=wait, owner_alive=owner_alive,
                step_complete=complete, step_total=len(steps),
                step_percent=(complete * 100 // len(steps)) if route in PATHS else None,
                elapsed_seconds=max(0, round(age(task['created_at'], stamp) - (age(task['completed_at'], stamp) if task['completed_at'] else 0))),
                latest=next((e for e in reversed(events) if e['stage'] in allowed), None))
    return task
