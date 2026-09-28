"""Small polling projections. Never load snapshots, reports or catalog history."""
import hashlib
import json
import math

TABLES={'baseline':'bulk_baseline_runs','improvement':'rsi_cycles','workflows':'strategic_workflow_runs'}
def _one(db,sql,args=()):
 c=db.execute(sql,args);r=c.fetchone();return dict(zip([d[0] for d in c.description],r)) if r else None

def status_response(db,kind,run_id=None,*,enabled=True,active_workers=None,limit=6):
 if kind not in TABLES:raise ValueError('Unknown status kind')
 table=TABLES[kind]
 if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():return None if run_id else {'runs':[],'enabled':enabled}
 if not run_id:
  ids=[r[0] for r in db.execute(f'SELECT id FROM {table} ORDER BY created_at DESC LIMIT ?', (min(6,max(1,int(limit))),))]
  result={'runs':[status_response(db,kind,i,enabled=enabled,active_workers=active_workers) for i in ids],'enabled':enabled}
  result['version']=status_version(result);return result
 r=_one(db,f'SELECT id,status,created_at,updated_at,error FROM {table} WHERE id=?',(run_id,))
 if not r:return None
 r['view']='status'
 if kind=='baseline':
  counts=dict(db.execute('SELECT status,COUNT(*) FROM bulk_baseline_documents WHERE run_id=? GROUP BY status',(run_id,)))
  totals=db.execute('SELECT COUNT(*),SUM(prepared_json IS NOT NULL),SUM(result_json IS NOT NULL),SUM(reused) FROM bulk_baseline_documents WHERE run_id=?',(run_id,)).fetchone()
  settings=db.execute("SELECT json_extract(settings_json,'$.batch_size'),json_extract(settings_json,'$.snapshot_coverage.empty_evidence') FROM bulk_baseline_runs WHERE id=?",(run_id,)).fetchone()
  workers=active_workers.get(run_id,0) if isinstance(active_workers,dict) else active_workers
  r['metrics']={'total':totals[0],'preprocessed':totals[1] or 0,'analyzed':totals[2] or 0,'verified':counts.get('verified',0),'pending':sum(counts.get(s,0) for s in ('pending','running','retry')),'failed':counts.get('failed',0),'needs_review':counts.get('needs_review',0),'active_workers':workers if workers is not None else math.ceil(counts.get('running',0)/(settings[0] or 24)),'reused_verified':totals[3] or 0,'empty_evidence':settings[1] or 0}
  r['last_event']=_one(db,'SELECT seq,stage,detail,created_at FROM bulk_baseline_events WHERE run_id=? ORDER BY seq DESC LIMIT 1',(run_id,))
 elif kind=='improvement':
  base=_one(db,"SELECT next_run_at,pause_requested,no_progress,json_extract(coverage_json,'$.total_unique') AS total,json_extract(coverage_json,'$.duplicates_excluded') AS duplicates,(SELECT COUNT(*) FROM json_each(seen_json)) AS seen FROM rsi_cycles WHERE id=?",(run_id,))
  r.update(next_run_at=base['next_run_at'],pause_requested=bool(base['pause_requested']))
  counts=_one(db,"""WITH current_scope AS MATERIALIZED (SELECT j.value FROM rsi_cycles c,json_each(c.coverage_json,'$.corpus_ids') j WHERE c.id=?) SELECT COUNT(*) AS processed,COALESCE(SUM(json_extract(s.value,'$.status')='verified'),0) AS verified,
    COALESCE(SUM(json_extract(s.value,'$.status')='needs_review'),0) AS review,COALESCE(SUM(json_extract(s.value,'$.status')='failed'),0) AS failed,
    COALESCE(SUM(COALESCE(json_extract(s.value,'$.risk_assessed'),0)),0) AS risk,COALESCE(SUM(CASE WHEN json_type(s.value,'$.risk_reviewed') IS NULL THEN COALESCE(json_extract(s.value,'$.risk_assessed'),0) ELSE COALESCE(json_extract(s.value,'$.risk_reviewed'),0) END),0) AS risk_reviewed,
    COALESCE(SUM(CASE WHEN json_type(s.value,'$.risk_reviewed') IS NULL THEN COALESCE(json_extract(s.value,'$.risk_assessed'),0) ELSE COALESCE(json_extract(s.value,'$.risk_reviewed'),0) END=1 AND COALESCE(json_extract(s.value,'$.risk_assessed'),0)=0),0) AS risk_insufficient,COALESCE(SUM(COALESCE(json_extract(s.value,'$.reused'),0)),0) AS reused
    FROM rsi_cycles c,json_each(c.state_json) s WHERE c.id=? AND
    (json_type(c.coverage_json,'$.corpus_ids') IS NULL OR s.key IN (SELECT value FROM current_scope))""",(run_id,run_id))
  rounds=db.execute("SELECT COUNT(*),SUM(status='complete') FROM rsi_rounds WHERE cycle_id=?",(run_id,)).fetchone();total=base['total'] or 0
  r['metrics']={'round_count':rounds[0],'completed_rounds':rounds[1] or 0,'no_progress_rounds':base['no_progress'],'seen_documents':base['seen'],'total_unique':total,'processed_unique':counts['processed'],'remaining':max(0,total-counts['processed']),'verified_unique':counts['verified'],'needs_review_unique':counts['review'],'failed_unique':counts['failed'],'verification_pending':max(0,total-counts['verified']),'risk_assessed_unique':counts['risk'],'risk_unassessed_unique':max(0,total-counts['risk']),'reused_verified':counts['reused'],'duplicates_excluded':base['duplicates'] or 0,'all_processed':bool(total) and counts['processed']==total,'all_verified':bool(total) and counts['verified']==total}
  workflow_table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategic_workflow_runs'").fetchone()
  active=db.execute("SELECT COUNT(DISTINCT w.id) FROM rsi_rounds r JOIN strategic_workflow_runs w ON w.id=r.workflow_run_id WHERE r.cycle_id=? AND r.status='running' AND w.status='running'",(run_id,)).fetchone()[0] if workflow_table else 0
  r['metrics'].update(active_workflows=active,risk_reviewed_unique=counts['risk_reviewed'],risk_information_insufficient=counts['risk_insufficient'])
  ledger_exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='corpus_completion_documents'").fetchone()
  if ledger_exists:
   ledger=dict(db.execute('SELECT status,COUNT(*) FROM corpus_completion_documents WHERE cycle_id=? GROUP BY status',(run_id,)))
   if ledger:
    r['metrics'].update(completion_total=sum(ledger.values()),**{'completion_'+key:ledger.get(key,0) for key in ('complete','pending','running','needs_review','failed')})
  r['last_event']=_one(db,'SELECT number,status,workflow_run_id,created_at,completed_at,error FROM rsi_rounds WHERE cycle_id=? ORDER BY number DESC LIMIT 1',(run_id,))
  r['current_workflow_id']=(r['last_event'] or {}).get('workflow_run_id')
 else:
  r['snapshot_count']=db.execute('SELECT json_array_length(snapshot_json) FROM strategic_workflow_runs WHERE id=?',(run_id,)).fetchone()[0]
  r['stages']=dict(db.execute('SELECT e.stage,e.status FROM strategic_workflow_events e WHERE e.run_id=? AND e.seq=(SELECT MAX(x.seq) FROM strategic_workflow_events x WHERE x.run_id=e.run_id AND x.stage=e.stage)',(run_id,)))
  r['last_event']=_one(db,'SELECT seq,stage,status,created_at,detail FROM strategic_workflow_events WHERE run_id=? ORDER BY seq DESC LIMIT 1',(run_id,))
 if r.get('last_event') and 'detail' in r['last_event']:r['last_event']['detail']=r['last_event']['detail'][:500]
 r['error']=(r.get('error') or '')[:1000]
 r['version']=status_version(r)
 return r

def status_version(value):
 """Hash the compact projection; callers may use this as the HTTP ETag."""
 return hashlib.sha256(json.dumps({k:v for k,v in value.items() if k!='version'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()[:24]
