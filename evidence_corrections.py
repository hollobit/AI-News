"""Read-only projection of explicit source withdrawals; archived audits stay intact."""
import hashlib
import json
from copy import deepcopy


def correction_token(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='risk_review_resolutions' AND type='table'").fetchone():
        return ()
    return tuple(tuple(row) for row in db.execute(
        'SELECT risk_id,status,reason,corrected_source_url,replacement_workflow_run_id,created_at '
        'FROM risk_review_resolutions ORDER BY risk_id,created_at'))


def withdrawn_refs(payload, run_id, token):
    records = {row[0]: row for row in token}
    result = {}
    for risk in (payload.get('risk_report') or {}).get('risks', []):
        risk_id = 'risk:' + hashlib.sha256(json.dumps([run_id, risk], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        row = records.get(risk_id)
        if not row or row[1] != 'withdrawn_source_mismatch' or not str(row[2] or '').strip():
            continue
        for ref in risk.get('evidence_ids', []):
            if isinstance(ref, str):
                result[ref] = {'risk_id':risk_id,'workflow_run_id':run_id,'status':row[1],
                    'reason':row[2],'corrected_source_url':row[3],
                    'replacement_workflow_run_id':row[4],'created_at':row[5]}
    return result


def project_catalog(db, items):
    token = correction_token(db)
    if not token:
        return items
    excluded = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_artifacts'").fetchone():
        for run, raw in db.execute("SELECT run_id,payload_json FROM strategic_workflow_artifacts WHERE stage='final'"):
            try:
                refs = withdrawn_refs(json.loads(raw), run, token)
            except (ValueError, TypeError):
                continue
            excluded.update({(run,ref):record for ref,record in refs.items()})
    output = deepcopy(items)
    for item in output:
        corrections = []
        for evidence in item.get('evidence', []):
            for run in evidence.get('workflow_run_ids', item.get('run_ids', [])):
                record = excluded.get((run,evidence.get('source_record_id')))
                if record and record not in corrections:
                    corrections.append(record)
        if corrections:
            original_evidence = item.get('evidence', [])
            original_ids = item.get('evidence_ids', [])
            original_runs = item.get('run_ids', [])
            retained = []
            if item.get('kind') in {'observed_keyword', 'strategic_concept'}:
                # Keywords aggregate independent observations. A proposed concept
                # retains only independent runs, never a rewritten partial claim.
                affected_runs = {record['workflow_run_id'] for record in corrections}
                for evidence in original_evidence:
                    runs = evidence.get('workflow_run_ids', original_runs)
                    valid_runs = [run for run in runs
                                  if (run, evidence.get('source_record_id')) not in excluded
                                  and not (item.get('kind') == 'strategic_concept' and run in affected_runs)]
                    if valid_runs:
                        retained.append(dict(evidence, workflow_run_ids=valid_runs))
            # Composite claim relations stay excluded in their entirety.
            item.update(lifecycle='withdrawn_source_provenance', retrieval_eligible=False,
                        corrections=corrections, historical_evidence=original_evidence,
                        historical_evidence_ids=original_ids, historical_run_ids=original_runs,
                        evidence=[], evidence_ids=[], document_ids=[], support_count=0)
            if retained:
                document_ids = sorted({e['document_id'] for e in retained if e.get('document_id')})
                item.update(lifecycle='partially_withdrawn_source_provenance', retrieval_eligible=True,
                            evidence=retained, evidence_ids=sorted({e['id'] for e in retained}),
                            document_ids=document_ids, support_count=len(document_ids),
                            run_ids=sorted({run for e in retained for run in e['workflow_run_ids']}))
            else:
                item['run_ids'] = []
    return output
