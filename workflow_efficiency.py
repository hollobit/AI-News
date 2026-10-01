"""Exact-input intermediate reuse and role-specific context projection.

Cached drafts are not published results: all existing admission gates still apply.
"""
import json
from model_policy import policy
from llm_runtime import infer_role

VERSION = 'workflow-efficiency-v1'


def role_context(role, context):
    common = {'coverage', 'request'}
    fields = {
        'national': common | {'keywords', 'graph_context'},
        'technology': common | {'keywords', 'graph_context'},
        'risk_assessment': common,
        'synthesis': common | {'analysts', 'deliberation', 'risk_report'},
        'verification': {'report', 'deliberation', 'request', 'revision_target_map'},
        'risk_verification': {'risk_report', 'request', 'revision_target_map'},
        'revision': common | {'report', 'critique', 'deliberation'},
        'risk_revision': common | {'risk_report', 'critique'},
    }
    return {key: value for key, value in context.items() if key in fields.get(role, context)}


def cached_call(service, run_id, stage, prompt, schema, validate, *, escalation=False):
    # Bounded lock stripes provide cross-process single-flight without expiring
    # a live owner's lease. flock is released by the OS on process exit.
    import fcntl
    import time
    from pathlib import Path
    from strategic_workflow import digest
    selected=policy(infer_role(prompt,schema),escalation=escalation)
    key=digest([VERSION,prompt,schema,selected])
    directory=Path(service.path).resolve().with_name(Path(service.path).name+'.stage-locks')
    directory.mkdir(exist_ok=True)
    with (directory/(key[:2]+'.lock')).open('a') as lock:
        while True:
            service._check()
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.1)
        return _cached_call(service,run_id,stage,prompt,schema,validate,escalation=escalation)


def _cached_call(service, run_id, stage, prompt, schema, validate, *, escalation=False):
    from strategic_workflow import digest
    selected = policy(infer_role(prompt, schema), escalation=escalation)
    key = digest([VERSION, prompt, schema, selected])
    with service.db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS workflow_stage_cache (
            input_hash TEXT PRIMARY KEY, source_run TEXT NOT NULL,
            stage TEXT NOT NULL, payload_json TEXT NOT NULL, policy_json TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS workflow_cache_observations(
            run_id TEXT,stage TEXT,input_hash TEXT,outcome TEXT,PRIMARY KEY(run_id,stage))''')
        row = db.execute('SELECT * FROM workflow_stage_cache WHERE input_hash=?', (key,)).fetchone()
    def observe(outcome):
        with service.db() as db:
            db.execute('INSERT OR REPLACE INTO workflow_cache_observations VALUES(?,?,?,?)',(run_id,stage,key,outcome))
    observe('miss_input' if not row else 'miss_validation')
    if row:
        try:
            result = validate(json.loads(row['payload_json']))
        except (ValueError, KeyError, TypeError):
            result = None
        if result is not None:
            observe('hit')
            service._save(run_id, 'reuse_' + stage, dict(input_hash=key, source_run=row['source_run'],
                          model=selected['model'], policy_version=selected['policy_version'], kind='intermediate'))
            return result
    with service.db() as db:
        db.execute('DELETE FROM strategic_workflow_artifacts WHERE run_id=? AND stage=?', (run_id, 'reuse_' + stage))
    result = validate(service._analyze(prompt, schema, escalation=escalation))
    # A rejected audit must be evaluated again if explicitly resubmitted.
    accepted = result.get('accepted', True) is True
    if 'verification' in result:
        accepted = result['verification'].get('accepted') is True and result['risk_verification'].get('accepted') is True
    if accepted:
        with service.db() as db:
            db.execute('INSERT OR REPLACE INTO workflow_stage_cache VALUES(?,?,?,?,?)',
                       (key, run_id, stage, json.dumps(result, ensure_ascii=False), json.dumps(selected)))
    return result
