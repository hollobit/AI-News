"""Read-only control room. Stored workflow events are history, not publication approval."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from statistics import median
from urllib.parse import quote

from decision_journal import path_for, source_refs
from operations_health import alive
from system_one import status as decision_status

ACTIVE = ('queued', 'preparing', 'running', 'planned')


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=.25)
    db.row_factory = sqlite3.Row
    return db


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def parsed(value, default=None):
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def stamp(value):
    if isinstance(value, (float, int)):
        return value
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def subject(db, available, kind, identity):
    if kind == 'workflow' and 'strategic_workflow_runs' in available:
        r = db.execute('''SELECT id,status,created_at,updated_at,
            json_extract(request_json,'$.owner_pid') AS owner,
            coalesce(json_extract(snapshot_json,'$[0].title'),json_extract(request_json,'$.question')) AS title
            FROM strategic_workflow_runs WHERE id=?''', (identity,)).fetchone()
        href='/operations#workflow'
    elif kind == 'question' and 'graph_question_jobs' in available:
        r = db.execute('''SELECT id,status,created_at,created_at AS updated_at,owner_pid AS owner,
            json_extract(request_json,'$.question') AS title FROM graph_question_jobs WHERE id=?''', (identity,)).fetchone()
        href='/context?answer='+quote(identity, safe='')
    elif kind == 'wiki' and 'wiki_topics' in available:
        r = db.execute('SELECT id,status,updated_at AS created_at,updated_at,owner_pid AS owner,id AS title FROM wiki_topics WHERE id=?', (identity,)).fetchone()
        href='/wiki'
    else:
        return None
    if not r:
        return None
    item=dict(r); owner=item.pop('owner')
    item.update(kind=kind,key=kind+':'+identity,href=href,title=str(item.get('title') or identity)[:240],recorded_status=item['status'])
    if item['status'] in ACTIVE and not alive(owner):item['status']='owner_missing'
    item['created_at']=stamp(item['created_at']);item['updated_at']=stamp(item['updated_at'])
    from .control_progress import current
    item['progress']=current(db,available,kind,identity,item['status'])
    return item


def recent_decisions(path):
    if not path_for(path).exists():return []
    with closing(connect(path_for(path))) as db:
        if 'decision_observations' not in tables(db):return []
        rows=db.execute('''SELECT id,subject_kind,subject_id,status,started_at,finished_at,cached,elapsed_ms,
            owner_pid,context_json,result_json,baseline_json FROM decision_observations ORDER BY id DESC LIMIT 80''').fetchall()
    result=[]
    for row in rows:
        r=dict(row);context=parsed(r.pop('context_json'),{});body=parsed(r.pop('result_json'),{})
        r['title']=context.get('title','');r['historical_comparison']=context.get('historical_comparison',False)
        r['usage']=body.get('usage',{})
        baseline=parsed(r.pop('baseline_json'),{})
        expected={'compact-v1':'compact','parallel-drafts-v1':'parallel','multi-role':'full'}.get(baseline.get('path'))
        actual=body.get('answers',{}).get('route',{}).get('choice')
        r['route_agrees']=(expected==actual) if expected and actual in ('compact','parallel','full') else None
        r['risk_signal']=body.get('answers',{}).get('risk',{}).get('choice') in ('required','unclear')
        if r['status']=='running' and not alive(r['owner_pid']):r['status']='owner_missing'
        r.pop('owner_pid');result.append(r)
    return result


def overview(path):
    result=dict(jobs=[],decisions=[],settings=decision_status(path),unavailable=False,window=80)
    try:
        observations=recent_decisions(path); jobs={}
        with closing(connect(path)) as db:
            available=tables(db)
            for kind,table,limit in [('workflow','strategic_workflow_runs',20),('question','graph_question_jobs',8),('wiki','wiki_topics',6)]:
                if table not in available:continue
                recent=[r[0] for r in db.execute('SELECT id FROM '+table+' ORDER BY rowid DESC LIMIT ?', (limit,))]
                active=[r[0] for r in db.execute("SELECT id FROM "+table+" WHERE status IN ('queued','preparing','running','planned') ORDER BY rowid DESC LIMIT 20")]
                for identity in dict.fromkeys(active+recent):
                    item=subject(db,available,kind,identity)
                    if item:jobs[item['key']]=item
            for obs in observations:
                key=obs['subject_kind']+':'+obs['subject_id']
                if key not in jobs and obs['subject_id']:
                    item=subject(db,available,obs['subject_kind'],obs['subject_id'])
                    if item:jobs[key]=item
                if key in jobs and 'decision' not in jobs[key]:jobs[key]['decision']=obs
        result['jobs']=sorted(jobs.values(),key=lambda j:(j['status'] in ACTIVE,j.get('updated_at') or 0),reverse=True)[:64]
        result['decisions']=observations
        calls=[r for r in observations if r['status']=='complete' and not r['cached']]
        measured=sorted(r['elapsed_ms'] for r in calls if r['elapsed_ms'] is not None)
        result['metrics']=dict(calls=len(calls),cached=sum(r['cached'] for r in observations),
            skipped=sum(r['status'] in ('skipped','busy') for r in observations),
            p50_ms=median(measured) if measured else None,
            input_tokens=sum(r['usage'].get('input_tokens',0) for r in calls),
            output_tokens=sum(r['usage'].get('output_tokens',0) for r in calls))
        compared=[r['route_agrees'] for r in calls if r['route_agrees'] is not None]
        result['metrics'].update(route_compared=len(compared),route_agreements=sum(compared))
    except (sqlite3.Error,OSError):result['unavailable']=True
    result['version']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()[:20]
    return result


def observations_for(path,kind,identity):
    if not path_for(path).exists():return []
    with closing(connect(path_for(path))) as db:
        if 'decision_observations' not in tables(db):return []
        rows=db.execute('''SELECT * FROM decision_observations WHERE subject_kind=? AND subject_id=? ORDER BY id DESC LIMIT 40''',(kind,identity)).fetchall()
    result=[]
    for row in reversed(rows):
        r=dict(row)
        for field in ('context','questions','baseline','result'):r[field]=parsed(r.pop(field+'_json'),{})
        if r['status']=='running' and not alive(r['owner_pid']):r['status']='owner_missing'
        r.pop('owner_pid');result.append(r)
    return result


def detail(path,kind,identity):
    with closing(connect(path)) as db:
        available=tables(db);job=subject(db,available,kind,identity)
        if not job:return None
        events=[];sources=[];call_ids=[];route=None;related=[];relations=[]
        def event(stage,status,when,key):
            when=stamp(when)
            if when is not None:events.append(dict(id=str(key),stage=stage,status=status,at=when))
        if kind=='workflow':
            from .workflow_tasks import PATHS,PREFIX,EXTRAS
            allowed=set(PREFIX+EXTRAS+['final']+sum(PATHS.values(),[]))
            if 'strategic_workflow_events' in available:
                rows=db.execute('SELECT seq,stage,status,created_at FROM strategic_workflow_events WHERE run_id=? ORDER BY seq DESC LIMIT 500',(identity,)).fetchall()
                for r in reversed(rows):
                    if r['stage'] in allowed:event(r['stage'],r['status'],r['created_at'],'workflow:'+str(r['seq']))
            if 'strategic_workflow_artifacts' in available:
                for r in db.execute("SELECT stage,payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage IN ('enrichment','execution_plan','graph_retrieval')",(identity,)):
                    body=parsed(r['payload_json'],{})
                    if r['stage']=='enrichment':sources=source_refs(body.get('evidence',[]))
                    elif r['stage']=='execution_plan':route=body.get('path')
                    elif r['stage']=='graph_retrieval':
                        graph=body.get('result',{})
                        related=[{k:n.get(k) for k in ('id','name','type')} for n in graph.get('nodes',[])[:20]]
                        ids={n['id'] for n in related}
                        relations=[{k:e.get(k) for k in ('source','target','relation','type')} for e in graph.get('edges',[])[:40]
                                   if e.get('source') in ids and e.get('target') in ids]
                call_ids=[r[0] for r in db.execute("SELECT json_extract(payload_json,'$.call_id') FROM strategic_workflow_artifacts WHERE run_id=? AND stage LIKE 'model_call_%' LIMIT 100",(identity,)) if type(r[0]) is int]
            if not sources:
                row=db.execute('SELECT snapshot_json FROM strategic_workflow_runs WHERE id=?',(identity,)).fetchone()
                value=parsed(row[0],[]);sources=source_refs(value if isinstance(value,list) else [])
        elif kind=='question':
            if 'graph_question_events' in available:
                rows=db.execute('SELECT seq,stage,status,phase,created_at FROM graph_question_events WHERE job_id=? ORDER BY seq DESC LIMIT 500',(identity,)).fetchall()
                for r in reversed(rows):event('review_'+r['phase'] if r['phase'] in ('repair','recheck','incomplete') else r['stage'],'needs_review' if r['phase']=='incomplete' else r['status'],r['created_at'],'question:'+str(r['seq']))
            if not events and 'graph_question_progress' in available:
                row=db.execute('SELECT body FROM graph_question_progress WHERE job_id=?',(identity,)).fetchone()
                for stage,entry in parsed(row[0] if row else None,{}).get('stages',{}).items():
                    event(stage,'running',entry.get('started_at'),'question:'+stage+':start')
                    event(stage,'complete',entry.get('completed_at'),'question:'+stage+':end')
        elif kind=='wiki':
            # Topic timestamps alone do not reconstruct past model stages.
            event('wiki',job['recorded_status'],job['updated_at'],'wiki:latest')
    observations=observations_for(path,kind,identity)
    for obs in observations:
        event('jev','running',obs['started_at'],'jev:'+str(obs['id'])+':start')
        if obs['finished_at']:event('jev',obs['status'],obs['finished_at'],'jev:'+str(obs['id'])+':end')
        if not sources:sources=obs['context'].get('sources',[])
    events.sort(key=lambda e:(e['at'],e['id']))
    calls=[];runtime=Path(path).parent/'llm_runtime.sqlite3'
    if call_ids and runtime.exists():
        try:
            with closing(connect(runtime)) as db:
                fields={r[1] for r in db.execute('PRAGMA table_info(llm_calls)')}
                wanted=[k for k in ('id','role','model','status','owner_pid','queued_at','started_at','finished_at','wait_ms','run_ms','input_tokens','output_tokens') if k in fields]
                calls=[dict(r) for r in db.execute('SELECT '+','.join(wanted)+' FROM llm_calls WHERE id IN ('+','.join('?' for _ in call_ids)+') ORDER BY id',call_ids)]
                for call in calls:
                    call['owner_alive']=alive(call.pop('owner_pid',None))
                    if call['status'] in ACTIVE and not call['owner_alive']:call['status']='owner_missing'
        except sqlite3.Error:pass
    # No draft, review prose, or generated relationship is returned as a verified answer.
    return dict(job=job,events=events,decisions=observations,sources=sources,route=route,calls=calls,
                related=related,relations=relations,history_limit=500,
                history_note='단계 최근500개·Jev 최근40회입니다. 과거 질의는 마지막 단계 기록만 남아 있을 수 있습니다. 저장 당시 입력·상태와 현재 공개 유효성은 다를 수 있습니다. 관련 노드는 당시 검색 단서입니다.')
