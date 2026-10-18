"""Baseline generation and independent review, with per-document admission."""
import json
import time
from bulk_baseline import (ANALYSIS_SCHEMA, REVIEW_SCHEMA, RECORD, validate_record, digest, js, now, pack_json)
from baseline_execution import key_for, read_draft, save_draft, discard


def execute(self,run_id,rows):
    started=time.monotonic();analysis_ms=review_ms=0;output_chars=0
    with self.db() as db:
        batch_settings=json.loads(db.execute('SELECT settings_json FROM bulk_baseline_runs WHERE id=?',(run_id,)).fetchone()[0])
    snapshots={r['document_id']:json.loads(r['snapshot_json']) for r in rows}
    preparations={r['document_id']:json.loads(r['prepared_json']) for r in rows}
    from baseline_citations import candidates, generation_schema, materialize, bind_documents
    citation_options={key:candidates(snapshots[key],preparations[key]) for key in snapshots}
    inputs=[];keys={};generation_provenance={};reused_drafts=0
    from engine_errors import infrastructure_error
    for row in rows:
        prior=json.loads(row['result_json']) if row.get('result_json') else {}
        feedback=[issue for issue in (prior.get('verification') or {}).get('issues',[]) if not infrastructure_error(issue)]
        if row.get('error') and not infrastructure_error(row['error']) and row['error'] not in feedback:feedback.append(row['error'])
        previous={'issues':feedback[:8],'previous_analysis':{k:prior[k] for k in RECORD['required'] if k in prior}} if feedback else None
        keys[row['document_id']]=key_for(self,snapshots[row['document_id']],preparations[row['document_id']],previous)
        inputs.append(dict(snapshots[row['document_id']],keyword_citations=citation_options[row['document_id']],
                           previous_review=previous))
    outcomes={};valid={};engine_failure=None
    try:
        generation_inputs=[]
        for entry in inputs:
            identity=entry['document_id']
            saved=read_draft(self,keys[identity],snapshots[identity],preparations[identity])
            if saved:
                valid[identity]=saved['record'];citation_options[identity]=saved['candidates']
                generation_provenance[identity]=saved.get('provenance');reused_drafts+=1
            else:generation_inputs.append(entry)
        decision_meta={'method':'checkpoint','removed':0}
        if generation_inputs:
            from baseline_decisions import filter_candidates
            missing={entry['document_id']:snapshots[entry['document_id']] for entry in generation_inputs}
            choices,decision_meta=filter_candidates(self.path,run_id,missing,{key:citation_options[key] for key in missing})
            citation_options.update(choices)
            for entry in generation_inputs:entry['keyword_citations']=citation_options[entry['document_id']]
            phase=time.monotonic()
            raw=self.analyzer(self._prompt('analysis',generation_inputs),bind_documents(generation_schema(ANALYSIS_SCHEMA,[key for choices in choices.values() for key in choices]),missing))
            analysis_ms=round((time.monotonic()-phase)*1000);output_chars+=len(js(raw))
            entries=raw.get('documents',[]) if isinstance(raw,dict) else []
        else:raw={};entries=[]
        for row in rows:
            identity=row['document_id']
            if identity in valid:continue
            matches=[r for r in entries if isinstance(r,dict) and r.get('document_id')==identity]
            try:
                if len(matches)!=1:raise ValueError('입력 문서가 누락되거나 중복되었습니다.')
                valid[identity]=validate_record(materialize(matches[0],citation_options[identity]),snapshots[identity],preparations[identity])
                generation_provenance[identity]=getattr(raw,'provenance',None)
                save_draft(self,keys[identity],snapshots[identity],valid[identity],citation_options[identity],generation_provenance[identity])
            except (ValueError,TypeError) as exc:outcomes[identity]=('failed',None,str(exc))
        if valid:
            review_inputs=[dict(snapshots[key],keyword_citations=citation_options[key],analysis=value) for key,value in valid.items()]
            phase=time.monotonic()
            response=self.analyzer(self._prompt('verification',review_inputs),bind_documents(REVIEW_SCHEMA,{key:snapshots[key] for key in valid},'reviews'))
            review_ms=round((time.monotonic()-phase)*1000);output_chars+=len(js(response))
            reviews=response.get('reviews',[]) if isinstance(response,dict) else []
            for identity,record in valid.items():
                matches=[r for r in reviews if isinstance(r,dict) and r.get('document_id')==identity]
                ids={e['id'] for e in snapshots[identity]['evidence']}
                if len(matches)!=1:
                    outcomes[identity]=('failed',None,'독립 검토에서 문서가 누락되거나 중복되었습니다.');continue
                audit=matches[0]
                checked=audit.get('checked_evidence_ids');issues=audit.get('issues')
                if (not isinstance(audit.get('accepted'),bool) or not isinstance(checked,list) or any(not isinstance(r,str) or r not in ids for r in checked)
                        or not isinstance(issues,list) or any(not isinstance(i,str) for i in issues)):
                    outcomes[identity]=('failed',None,'독립 검토 응답·인용 형식 오류');continue
                if not set(record['evidence_ids'])<=set(checked):issues.append('모든 분석 인용을 대조하지 않았습니다.')
                accepted=audit['accepted'] and not issues
                audit=dict(audit,accepted=accepted,report_hash=digest(record),evidence_hash=digest(snapshots[identity]['evidence']))
                result=dict(record,verification=audit,verified=accepted,run_id=run_id,source_scope=snapshots[identity]['source_scope'],
                            evidence=snapshots[identity]['evidence'],input_hash=snapshots[identity]['input_hash'],sectors=preparations[identity]['sectors'],
                            strategic_value=preparations[identity]['strategic_value'],
                            model_provenance=[p for p in (generation_provenance.get(identity),getattr(response,'provenance',None)) if p],
                            execution={'profile':batch_settings.get('execution_profile','micro-v1' if batch_settings.get('micro_batches') else 'legacy'),
                                       'draft_checkpoint_reused':identity not in {e['document_id'] for e in generation_inputs}})
                outcomes[identity]=('verified' if accepted else 'needs_review',result,'; '.join(issues)[:500])
    except Exception as exc:
        from engine_errors import infrastructure_error
        engine_failure = infrastructure_error(str(exc))
        if engine_failure:
            self.stop.set()
        for row in rows:outcomes.setdefault(row['document_id'],('failed',None,str(exc)[:500]))
    for identity,record in valid.items():
        status,result,error=outcomes.get(identity,('failed',None,'독립 검토 미완료'))
        if result is None:
            draft=dict(record,verified=False,run_id=run_id,source_scope=snapshots[identity]['source_scope'],
                       evidence=snapshots[identity]['evidence'],input_hash=snapshots[identity]['input_hash'],
                       verification={'accepted':False,'status':'not_completed','issues':[error],'checked_evidence_ids':[]})
            outcomes[identity]=(status,draft,error)
    with self.db() as db:
        for row in rows:
            identity=row['document_id'];status,result,error=outcomes.get(identity,('failed',None,'분석 결과 없음'))
            if engine_failure and status != 'verified':
                # An unavailable engine is not a content rejection. Preserve
                # the checkpoint and record the interrupted call separately.
                db.execute("UPDATE bulk_baseline_documents SET status='pending',attempts=?,error=?,updated_at=? WHERE run_id=? AND document_id=?",
                           (row['attempts'], error, now(), run_id, identity))
                continue
            # Semantic rejection is never eligible for generation reuse. The
            # next attempt receives the rejected result and its original issues.
            discard(db,keys[identity])
            if status!='verified' and row['attempts']+1<row.get('attempt_limit',2):status='retry'
            db.execute('UPDATE bulk_baseline_documents SET status=?,result_json=?,error=?,updated_at=? WHERE run_id=? AND document_id=?',
                       (status,pack_json(db,result) if result else None,error,now(),run_id,identity))
            if status=='verified':
                db.execute('INSERT OR REPLACE INTO bulk_baseline_cache VALUES (?,?,?,?,?,?)',
                           (row['input_hash'],identity,js(result),js(snapshots[identity]['evidence']),row['prepared_json'],now()))
        if engine_failure:
            db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES (?,?,?,?)',
                       (run_id,'engine_paused',js({'code':engine_failure,'documents':[r['document_id'] for r in rows]}),now()))
        db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES (?,?,?,?)',
                   (run_id,'analysis_and_verification',js({'documents':len(rows),'verified':sum(v[0]=='verified' for v in outcomes.values()),
                    'input_chars':len(js(inputs)),'output_chars':output_chars,'analysis_ms':analysis_ms,'review_ms':review_ms,
                    'elapsed_ms':round((time.monotonic()-started)*1000),'retry_documents':sum(r['attempts']>0 for r in rows),
                    'measurement_version':1,'workers':batch_settings['workers'],
                    'batch_mode':'micro' if batch_settings.get('micro_batches') else 'adaptive' if batch_settings.get('adaptive_batches') else 'fixed',
                    'generation_documents':len(generation_inputs) if 'generation_inputs' in locals() else 0,
                    'reused_drafts':reused_drafts,'keyword_decision':decision_meta if 'decision_meta' in locals() else {},
                    'configured_batch_size':batch_settings['batch_size']}),now()))
