import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from corpus_completion import CompletionRunner,connect,init
from recursive_improvement import RecursiveImprovementService,now
from strategic_workflow import digest
from improvement_selection import SelectionBatch
from completion_quality import message_text


class FakeWorkflow:
    barrier=None;calls=[];reject_groups=False;reject_all=False;active_count=0;maximum=0;lock=threading.Lock()
    enabled=True
    def __init__(self,path,**kwargs):self.path=str(path);self.active=None
    def create_run(self,items,request):
        with self.lock:
            self.__class__.active_count+=1;self.__class__.maximum=max(self.maximum,self.active_count)
            self.calls.append((len(items),request))
        try:
            if self.barrier:self.barrier.wait(3)
            identity=uuid.uuid4().hex
            evidence=[{'id':'e'+str(index),'text':message_text(item),'url':item['source_url'],'origin':'telegram_excerpt','title':item['title']} for index,item in enumerate(items)]
            report={'summary':'원문 범위 분석','claims':[{'title':entry['title'],'detail':'원문에서 확인한 설명','category':'watch_signal','uncertainty':'추가 사실 확인 필요','evidence_ids':[entry['id']]} for entry in evidence],'limitations':[]}
            risk={'summary':'위험 평가','risks':[],'assessed_evidence_ids':[entry['id'] for entry in evidence],'not_assessable_evidence_ids':[],'limitations':[]}
            accepted=not self.reject_all and not(self.reject_groups and len(items)>1)
            audit=lambda value:{'accepted':accepted,'issues':[] if accepted else ['원문 주장과 해석을 분리해야 합니다.'],'checked_evidence_ids':[e['id'] for e in evidence], 'limitations':[], 'report_hash':digest(value),'evidence_hash':digest(evidence)}
            result={'verified':accepted,'risk_verified':accepted,'report':report,'risk_report':risk,'evidence':evidence,'verification':audit(report),'risk_verification':audit(risk),'coverage':{'failed_urls':[]}}
            with connect(self.path) as db:
                db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?,?,?)',(identity,'complete' if accepted else 'needs_review',now(),now(),'[]',json.dumps(dict(request,owner_pid=os.getpid())),''))
                db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(identity,'final',json.dumps(result)))
            return self.get_run(identity)
        finally:
            with self.lock:self.__class__.active_count-=1
    def get_run(self,id):
        with connect(self.path) as db:
            row=db.execute('SELECT * FROM strategic_workflow_runs WHERE id=?',(id,)).fetchone()
            if not row:return None
            final=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(id,)).fetchone()
            return dict(row,results=json.loads(final[0]) if final else {})
    def resume(self,id):raise AssertionError('Completed checkpoint must not be rerun')
    def close(self):pass


class CompletionTests(unittest.TestCase):
    def test_capacity_defers_only_failed_checkpoint_and_survives_restart(self):
        runner=CompletionRunner(self.path,'cycle',batch_size=1);runner.prepare()
        planned=runner.plan()
        with patch('corpus_completion.time.time',return_value=1000):
            runner.record(planned,{'status':'failed','error':'[engine:capacity]'})
        self.assertFalse(runner.stop.is_set())
        restarted=CompletionRunner(self.path,'cycle',batch_size=1)
        with patch('corpus_completion.time.time',return_value=1010):
            other=restarted.plan()
        self.assertNotEqual(other['id'],planned['id'])
        with patch('corpus_completion.time.time',return_value=1031):
            retry=restarted.plan()
        self.assertEqual(retry['id'],planned['id'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT attempts FROM corpus_completion_documents WHERE position=0').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM completion_engine_events').fetchone()[0],1)

    def test_three_consecutive_engine_failures_pause_even_across_restart(self):
        runner=CompletionRunner(self.path,'cycle',batch_size=1);runner.prepare()
        for n in range(3):
            runner=CompletionRunner(self.path,'cycle',batch_size=1)
            with patch('corpus_completion.time.time',return_value=1000+n):
                planned=runner.plan()
                runner.record(planned,{'status':'failed','error':'[engine:capacity]'})
            self.assertEqual(runner.stop.is_set(),n==2)

    def test_hard_engine_failure_still_stops_immediately(self):
        runner=CompletionRunner(self.path,'cycle',batch_size=1);runner.prepare()
        runner.record(runner.plan(),{'status':'failed','error':'[engine:authentication]'})
        self.assertTrue(runner.stop.is_set())

    def test_failed_document_does_not_stop_other_documents(self):
        runner=CompletionRunner(self.path,'cycle',workers=2,batch_size=1,max_rounds=20)
        original=runner.work
        def work(service,planned):
            if planned['number']==1:
                return planned,{'status':'failed','error':'[engine:capacity]'}
            return original(service,planned)
        with patch.object(runner,'work',side_effect=work):runner.run()
        self.assertEqual(runner.summary()['counts'],{'complete':5,'running':1})
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM rsi_cycles').fetchone()[0],'waiting')
            self.assertEqual(db.execute('SELECT error FROM completion_engine_events ORDER BY seq DESC LIMIT 1').fetchone()[0],'')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'news.db'
        FakeWorkflow.calls=[];FakeWorkflow.barrier=None;FakeWorkflow.reject_groups=False;FakeWorkflow.reject_all=False;FakeWorkflow.maximum=FakeWorkflow.active_count=0
        self.ledger=RecursiveImprovementService(self.path,FakeWorkflow(self.path),lambda *_:[])
        with connect(self.path) as db:
            db.execute("INSERT INTO rsi_cycles(id,status,settings_json,created_at,updated_at) VALUES ('cycle','paused','{}',?,?)",(now(),now()))
            db.execute('CREATE TABLE strategic_workflow_runs(id TEXT PRIMARY KEY,status TEXT,created_at TEXT,updated_at TEXT,snapshot_json TEXT,request_json TEXT,error TEXT)')
            db.execute('CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT,PRIMARY KEY(run_id,stage))')
        self.items=SelectionBatch([{'title':'뉴스 '+str(i),'text':'AI 산업 소식','source_url':f'https://example.com/{i}'} for i in range(6)],coverage={'total_unique':6})
        self.patches=[patch('improvement_selection.all_corpus_items',lambda db:self.items),
            patch('source_enrichment.SourceService',lambda path:type('Sources',(),{'close':lambda self:None})()),
            patch('source_enrichment.attach_sources',lambda db,items:items),
            patch('strategic_workflow.WorkflowService',FakeWorkflow),
            patch('improvement_memory.improve_catalog',lambda *args:{'accepted':True,'followup_tasks':[],'added':[],'updated':[]})]
        for context in self.patches:context.start()
    def tearDown(self):
        for context in reversed(self.patches):context.stop()
        self.ledger.close();self.temp.cleanup()

    def test_legacy_checkpoint_is_resumed_without_resetting_attempts(self):
        runner=CompletionRunner(self.path,'cycle');runner.prepare()
        planned=runner.plan()
        with connect(self.path) as db:
            snapshot=json.loads(db.execute('SELECT snapshot_json FROM rsi_rounds WHERE id=?',(planned['id'],)).fetchone()[0])
            snapshot.pop('completion_attempt')
            db.execute('UPDATE rsi_rounds SET snapshot_json=?,workflow_run_id=? WHERE id=?',(json.dumps(snapshot),'old-workflow',planned['id']))
            db.execute('UPDATE corpus_completion_documents SET attempts=2')
        recovered=CompletionRunner(self.path,'cycle').plan()
        self.assertEqual(recovered['id'],planned['id'])
        self.assertEqual(recovered['workflow_run_id'],'old-workflow')
        self.assertEqual(recovered['snapshot']['completion_attempt'],2)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT min(attempts) FROM corpus_completion_documents').fetchone()[0],2)

    def test_recent_first_keeps_backlog_turn_and_bounded_run(self):
        for i,item in enumerate(self.items):item['day']=f'2026-09-{i+1:02d}'
        runner=CompletionRunner(self.path,'cycle',workers=1,batch_size=1,recent_first=True,max_rounds=2)
        runner.run()
        with connect(self.path) as db:
            rounds=[json.loads(r[0]) for r in db.execute('SELECT snapshot_json FROM rsi_rounds ORDER BY number')]
            self.assertEqual([r['items'][0]['day'] for r in rounds],['2026-09-06','2026-09-05'])
            self.assertEqual(tuple(db.execute('SELECT status,pause_requested FROM rsi_cycles').fetchone()),('waiting',0))

    def test_changed_input_archives_history_but_same_rejection_keeps_attempts(self):
        runner=CompletionRunner(self.path,'cycle');runner.prepare()
        with connect(self.path) as db:
            db.execute("UPDATE corpus_completion_documents SET status='needs_review',attempts=3")
            db.execute("UPDATE rsi_cycles SET status='paused'")
        self.items[0]['text']='변경된 원문'
        runner.prepare()
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM completion_input_history').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM corpus_completion_documents WHERE attempts=3 AND status='needs_review'").fetchone()[0],5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM corpus_completion_documents WHERE attempts=0 AND status='pending'").fetchone()[0],1)

    def test_engine_stop_is_not_user_pause(self):
        runner=CompletionRunner(self.path,'cycle',workers=1,batch_size=1)
        with patch.object(runner,'work',lambda service,planned:(planned,{'status':'failed','error':'[engine:timeout]'})):
            runner.run()
        with connect(self.path) as db:
            self.assertEqual(tuple(db.execute('SELECT status,pause_requested FROM rsi_cycles').fetchone()),('paused',0))

    def test_resume_appends_new_documents_once(self):
        runner=CompletionRunner(self.path,'cycle')
        runner.prepare()
        extra=dict(self.items[0],source_url='https://example.com/new')
        self.items.append(extra)
        for _ in range(2):
            with connect(self.path) as db:db.execute("UPDATE rsi_cycles SET status='paused'")
            runner.prepare()
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM corpus_completion_documents').fetchone()[0],7)
            coverage=json.loads(db.execute('SELECT coverage_json FROM rsi_cycles').fetchone()[0])
            self.assertEqual(len(coverage['corpus_ids']),7)

    def test_review_first_selects_retry_before_new(self):
        runner=CompletionRunner(self.path,'cycle',review_first=True)
        runner.prepare()
        with connect(self.path) as db:
            db.execute("UPDATE corpus_completion_documents SET status='needs_review',attempts=1 WHERE position=5")
        planned=runner.plan()
        self.assertEqual(len(planned['snapshot']['items']),1)
        self.assertEqual(planned['snapshot']['items'][0]['source_url'],'https://example.com/5')

    def test_exited_worker_nonterminal_state_is_infrastructure_failure(self):
        runner=CompletionRunner(self.path,'cycle')
        runner.prepare();planned=runner.plan()
        class ExitedWorker:
            active=None
            def create_run(self,*args):return {'id':'exited','status':'running'}
            def get_run(self,*args):return {'id':'exited','status':'running'}
        planned,result=runner.work(ExitedWorker(),planned)
        runner.record(planned,result)
        self.assertTrue(runner.stop.is_set())
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM rsi_rounds WHERE id=?',(planned['id'],)).fetchone()[0],'planned')
            self.assertEqual(db.execute("SELECT count(*) FROM corpus_completion_documents WHERE status='needs_review'").fetchone()[0],0)

    def test_six_parallel_workers_and_complete_admission(self):
        FakeWorkflow.barrier=threading.Barrier(6)
        runner=CompletionRunner(self.path,'cycle',workers=6,batch_size=1);runner.run()
        self.assertEqual(FakeWorkflow.maximum,6)
        self.assertEqual(runner.summary()['counts'],{'complete':6})
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM rsi_cycles').fetchone()[0],'complete')
            self.assertEqual(db.execute("SELECT count(*) FROM rsi_rounds WHERE status='complete'").fetchone()[0],6)

    def test_ten_workers_claim_distinct_documents(self):
        self.items=SelectionBatch([dict(self.items[0],source_url=f'https://example.com/{i}') for i in range(10)],coverage={'total_unique':10})
        FakeWorkflow.barrier=threading.Barrier(10)
        runner=CompletionRunner(self.path,'cycle',workers=10,batch_size=1,max_rounds=20)
        runner.run()
        self.assertEqual(FakeWorkflow.maximum,10)
        self.assertEqual(runner.summary()['counts'],{'complete':10})
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM rsi_rounds WHERE status='complete'").fetchone()[0],10)

    def test_failed_groups_shrink_without_repeating_admitted_documents(self):
        FakeWorkflow.reject_groups=True
        self.items=SelectionBatch([dict(self.items[0],source_url=f'https://example.com/{i}') for i in range(7)],coverage={'total_unique':7})
        runner=CompletionRunner(self.path,'cycle',workers=1,batch_size=24);runner.run()
        self.assertEqual([size for size,_ in FakeWorkflow.calls][:3],[7,6,1])
        self.assertEqual(runner.summary()['counts'],{'complete':7})
        with connect(self.path) as db:
            attempts=[r[0] for r in db.execute('SELECT attempts FROM corpus_completion_documents')]
        self.assertEqual(sorted(attempts),[2,3,3,3,3,3,3])
        retry_request=FakeWorkflow.calls[1][1]
        self.assertTrue(retry_request['improvement_context']['followup_tasks'][0]['text'])

    def test_exhausted_rejections_are_never_complete(self):
        FakeWorkflow.reject_all=True;self.items=SelectionBatch(self.items[:1],coverage={'total_unique':1})
        runner=CompletionRunner(self.path,'cycle',workers=1,batch_size=24);runner.run()
        self.assertEqual(len(FakeWorkflow.calls),3)
        self.assertEqual(runner.summary()['counts'],{'needs_review':1})
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT status FROM rsi_cycles').fetchone()[0],'needs_review')

    def test_completed_checkpoint_is_recorded_without_repeat_or_attempt_charge(self):
        self.items=SelectionBatch(self.items[:1],coverage={'total_unique':1})
        first=CompletionRunner(self.path,'cycle',workers=1);first.prepare();planned=first.plan()
        service=FakeWorkflow(self.path);first.work(service,planned)
        with connect(self.path) as db:db.execute("UPDATE rsi_cycles SET status='paused'")
        runner=CompletionRunner(self.path,'cycle',workers=1);runner.run()
        self.assertEqual(len(FakeWorkflow.calls),1)
        self.assertEqual(runner.summary()['counts'],{'complete':1})
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT attempts FROM corpus_completion_documents').fetchone()[0],1)

    def test_existing_verified_current_run_is_reused(self):
        FakeWorkflow(self.path).create_run(self.items,{})
        runner=CompletionRunner(self.path,'cycle',workers=1);runner.run()
        self.assertEqual(len(FakeWorkflow.calls),1)
        self.assertEqual(runner.summary()['counts'],{'complete':6})
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT SUM(attempts) FROM corpus_completion_documents').fetchone()[0],0)

    def test_pause_finishes_six_inflight_jobs_without_starting_more(self):
        self.items=SelectionBatch([dict(self.items[0],source_url=f'https://example.com/{i}') for i in range(8)],coverage={'total_unique':8})
        FakeWorkflow.barrier=threading.Barrier(6)
        original=FakeWorkflow.create_run
        def pause_then_run(service,items,request):
            with connect(service.path) as db:db.execute('UPDATE rsi_cycles SET pause_requested=1')
            return original(service,items,request)
        with patch.object(FakeWorkflow,'create_run',pause_then_run):
            runner=CompletionRunner(self.path,'cycle',workers=6,batch_size=1);runner.run()
        self.assertEqual(len(FakeWorkflow.calls),6)
        self.assertEqual(runner.summary()['counts'],{'complete':6,'pending':2})
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT status FROM rsi_cycles').fetchone()[0],'paused')

    def test_live_owner_refused_and_dead_owner_can_recover(self):
        with connect(self.path) as db:db.execute("UPDATE rsi_cycles SET status='running',owner_pid=?",(os.getpid(),))
        runner=CompletionRunner(self.path,'cycle')
        with self.assertRaises(RuntimeError):runner.prepare()
        with connect(self.path) as db:db.execute("UPDATE rsi_cycles SET owner_pid=999999999")
        runner.prepare()
        self.assertEqual(runner.summary()['counts'],{'pending':6})

    def test_prepare_seeds_real_critique_without_reusing_verdict(self):
        FakeWorkflow.reject_all=True
        old=FakeWorkflow(self.path).create_run(self.items,{})
        with connect(self.path) as db:db.execute('UPDATE strategic_workflow_runs SET snapshot_json=? WHERE id=?',(json.dumps(old['results']['evidence']),old['id']))
        runner=CompletionRunner(self.path,'cycle');runner.prepare()
        with connect(self.path) as db:
            rows=db.execute('SELECT status,attempts,admission_json FROM corpus_completion_documents').fetchall()
        self.assertTrue(all(row[0]=='pending' and row[1]==0 for row in rows))
        self.assertTrue(all(json.loads(row[2])['issues'] for row in rows))
        self.assertTrue(all(json.loads(row[2])['verified'] is False for row in rows))

    def test_feedback_backfill_is_opt_in_backed_up_and_pending_only(self):
        import completion_feedback as module
        runner=CompletionRunner(self.path,'cycle');runner.prepare()
        FakeWorkflow.reject_all=True;old=FakeWorkflow(self.path).create_run(self.items,{})
        with connect(self.path) as db:
            db.execute('UPDATE strategic_workflow_runs SET snapshot_json=? WHERE id=?',(json.dumps(old['results']['evidence']),old['id']))
            db.execute("UPDATE corpus_completion_documents SET status='running',attempts=1 WHERE position=0")
            db.execute("UPDATE corpus_completion_documents SET status='complete' WHERE position=1")
        dry=module.backfill(self.path,'cycle')
        self.assertEqual(dry['feedback_rows'],4)
        with connect(self.path) as db:self.assertTrue(all(row[0]=='{}' for row in db.execute('SELECT admission_json FROM corpus_completion_documents')))
        backup=Path(self.temp.name)/'feedback.jsonl'
        applied=module.backfill(self.path,'cycle',apply=True,backup_path=backup)
        self.assertEqual(applied['feedback_rows'],4)
        self.assertEqual(len(backup.read_text().splitlines()),4)
        with connect(self.path) as db:
            untouched=db.execute('SELECT admission_json FROM corpus_completion_documents WHERE position<2').fetchall()
        self.assertEqual([row[0] for row in untouched],['{}','{}'])
        self.assertEqual(module.backfill(self.path,'cycle',apply=True)['feedback_rows'],0)

    def test_every_third_persisted_plan_selects_review_despite_new_backlog(self):
        self.items=SelectionBatch([dict(self.items[0],source_url=f'https://example.com/{index}') for index in range(50)],coverage={'total_unique':50})
        runner=CompletionRunner(self.path,'cycle',batch_size=16);runner.prepare()
        with connect(self.path) as db:db.execute("UPDATE corpus_completion_documents SET status='needs_review',attempts=1 WHERE position=0")
        for number in (1,2):
            planned=runner.plan()
            self.assertEqual(planned['number'],number)
            self.assertEqual(planned['snapshot']['completion_attempt'],1)
            self.assertEqual(planned['snapshot']['preferred_queue'],'new')
            with connect(self.path) as db:
                db.execute("UPDATE rsi_rounds SET status='complete' WHERE id=?",(planned['id'],))
                db.execute("UPDATE corpus_completion_documents SET status='complete' WHERE status='running'")
        # A fresh planner uses persisted round numbering rather than an in-memory counter.
        restarted=CompletionRunner(self.path,'cycle',batch_size=16)
        repair=restarted.plan()
        self.assertEqual(repair['number'],3)
        self.assertEqual(repair['snapshot']['preferred_queue'],'review')
        self.assertEqual(repair['snapshot']['completion_attempt'],2)
        self.assertEqual(repair['snapshot']['identities'],['https://example.com/0'])
        self.assertEqual(restarted.summary()['counts']['pending'],17)

    def test_pause_preserves_unplanned_documents(self):
        runner=CompletionRunner(self.path,'cycle',workers=1,batch_size=1);runner.stop.set();runner.run()
        self.assertEqual(FakeWorkflow.calls,[])
        self.assertEqual(runner.summary()['counts'],{'pending':6})
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT status FROM rsi_cycles').fetchone()[0],'paused')

    def test_existing_complete_ledger_reconciles_polluted_risk_without_rewriting_history(self):
        self.items=SelectionBatch(self.items[:2],coverage={'total_unique':2})
        runner=CompletionRunner(self.path,'cycle',workers=1,batch_size=1);runner.run()
        with connect(self.path) as db:
            row=db.execute('SELECT * FROM corpus_completion_documents ORDER BY position LIMIT 1').fetchone()
            old=json.loads(db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(row['workflow_run_id'],)).fetchone()[0])
            old['risk_report']['limitations']=[old['evidence'][0]['id']]
            old['risk_verification']['report_hash']=digest(old['risk_report'])
            saved=json.dumps(old)
            db.execute("UPDATE strategic_workflow_artifacts SET payload_json=? WHERE run_id=? AND stage='final'",(saved,row['workflow_run_id']))
            db.execute('UPDATE corpus_completion_documents SET attempts=3 WHERE document_id=?',(row['document_id'],))
            db.execute("UPDATE rsi_cycles SET status='paused'")
        runner.prepare()
        with connect(self.path) as db:
            rows=db.execute('SELECT * FROM corpus_completion_documents ORDER BY position').fetchall()
            self.assertEqual([entry['status'] for entry in rows],['needs_review','complete'])
            self.assertEqual(rows[0]['attempts'],2)
            admission=json.loads(rows[0]['admission_json'])
            self.assertTrue(any('근거 ID만 기록됨' in issue for issue in admission['issues']))
            self.assertEqual(db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(row['workflow_run_id'],)).fetchone()[0],saved)
            db.execute("UPDATE rsi_cycles SET status='paused'")
        runner.prepare()
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT attempts FROM corpus_completion_documents WHERE document_id=?',(row['document_id'],)).fetchone()[0],2)
