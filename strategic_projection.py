"""Source-to-decision projections without model services or job recovery."""
from datetime import date
import json

from strategic_store import sync_store
from strategic_records import now, digest, refresh_record
from strategic_assessments import sync_assessments, risk_timeline


def entities(db, kind):
    return [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM intel_entities WHERE kind=?', (kind,))]


def token(db):
    from evidence_corrections import correction_token
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    revision=list(map(tuple,db.execute('SELECT * FROM projection_revisions'))) if 'projection_revisions' in tables else None
    operations=db.execute("SELECT COALESCE(max(seq),0) FROM intel_history WHERE id IN (SELECT id FROM intel_entities WHERE kind='operation')").fetchone()[0]
    return digest([revision,correction_token(db),operations,date.today().isoformat()]) if revision else None


def refresh_records(db):
    docs={item['id']:item for item in entities(db,'document')}
    topics={item['id']:item for item in entities(db,'topic')}
    for row in db.execute("SELECT payload_json FROM intel_records WHERE kind IN ('decision','scenario')").fetchall():
        item=json.loads(row[0]);topic=topics.get(item.get('topic_id'),{})
        refresh_record(db,item,docs.get,topic.get('document_ids',[]))


def refresh(db, *, previous_token=None, items=None, force=False, runtime=None):
    """Preserve the existing transaction, source gates and review-state updates."""
    current=token(db)
    runtime=dict(runtime or {'status':'preparing','last_success':None,'error':None})
    if items is None and not force and current and current==previous_token:
        refresh_records(db)
        return current,runtime
    result=sync_store(db,items)
    documents=entities(db,'document');claims=entities(db,'claim')
    from risk_views import _all_risks
    risks=_all_risks(db)['risks']
    result['changed_assessments']=sync_assessments(db,documents,claims,risks)
    result['changed_risk_observations']=risk_timeline(db,risks)
    refresh_records(db)
    runtime.update(status='ready',last_success=now(),error=None,changes=result)
    db.execute('INSERT OR REPLACE INTO intel_runtime VALUES (?,?)',('status',json.dumps(runtime,ensure_ascii=False)))
    return current,runtime


def prepare(db, previous_token=None):
    row=db.execute("SELECT payload_json FROM intel_runtime WHERE key='status'").fetchone()
    current,runtime=refresh(db,previous_token=previous_token,runtime=json.loads(row[0]) if row else None)
    db.execute('INSERT OR REPLACE INTO intel_runtime VALUES (?,?)',('projection_token',json.dumps(current)))
    return runtime
