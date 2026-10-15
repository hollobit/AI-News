"""Small, read-only current stage summaries for the automatic control view."""
import json
from datetime import datetime

from .workflow_tasks import PATHS, PREFIX, EXTRAS

ACTIVE = ('running', 'preparing', 'queued', 'planned')


def timestamp(value):
    if isinstance(value, (int, float)):return value
    try:return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError, TypeError):return None


def current(db, tables, kind, identity, status):
    steps={}; expected=[]; revision=None; bounded=False; known=False
    if kind=='workflow' and 'strategic_workflow_events' in tables:
        rows=db.execute('SELECT seq,stage,status,created_at FROM strategic_workflow_events WHERE run_id=? ORDER BY seq DESC LIMIT 201',(identity,)).fetchall()
        bounded=len(rows)>200
        allowed=set(PREFIX+EXTRAS+['final']+sum(PATHS.values(),[]))
        for r in reversed(rows[:200]):
            if r['stage'] in allowed:
                steps[r['stage']]=dict(stage=r['stage'],status=r['status'],at=timestamp(r['created_at']))
        revision=rows[0]['seq'] if rows else None
        route=None
        if 'strategic_workflow_artifacts' in tables:
            row=db.execute("SELECT json_extract(payload_json,'$.path') FROM strategic_workflow_artifacts WHERE run_id=? AND stage='execution_plan'",(identity,)).fetchone()
            route=row[0] if row else None
        known=route in PATHS and not bounded
        expected=PREFIX+PATHS.get(route,[])+[s for s in steps if s in EXTRAS]+['final']
    elif kind=='question' and 'graph_question_progress' in tables:
        from question_progress import STAGES
        row=db.execute('SELECT body,updated_at FROM graph_question_progress WHERE job_id=?',(identity,)).fetchone()
        if row:
            try:body=json.loads(row['body'])
            except (ValueError,TypeError):body={}
            revision=row['updated_at'];expected=list(STAGES);known=True
            for s,e in body.get('stages',{}).items():
                if s not in STAGES:continue
                state='needs_review' if e.get('phase')=='incomplete' else 'complete' if e.get('completed_at') else 'running'
                steps[s]=dict(stage=s,status=state,at=timestamp(e.get('completed_at') or e.get('started_at')),
                              phase=e.get('phase') if s=='review' else None)
    elif kind=='wiki':
        steps['wiki']=dict(stage='wiki',status=status,at=None)
    for s in steps.values():
        if s['status']=='running' and status not in ACTIVE:
            s['status']='owner_missing' if status=='owner_missing' else 'interrupted'
    done=sum(steps.get(s,{}).get('status')=='complete' for s in expected)
    percent=done*100//len(expected) if known and expected else None
    if percent==100 and status!='complete':percent=99
    return dict(steps=list(steps.values()),active=[s for s in steps.values() if s['status']=='running'],
                completed=done,total=len(expected) if known else None,percent=percent,
                revision=revision,truncated=bounded)
