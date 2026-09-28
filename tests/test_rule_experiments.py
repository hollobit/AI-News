import tempfile
from pathlib import Path
import unittest
from rule_experiments import RuleExperimentService,active_rules,digest,METRICS

class ExperimentTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test.db';self.services=[]
    def tearDown(self):
        for service in self.services:
            service.close()
            if service.thread:service.thread.join(3)
        self.temp.cleanup()
    def service(self,executor):
        value=RuleExperimentService(self.path,executor=executor,enabled=True);self.services.append(value);return value
    def cases(self):return [{'id':split,'split':split,'evidence':[{'id':split,'text':'원문 '+split,'origin':'telegram_excerpt','url':'https://example.com/'+split}]} for split in ('development','holdout')]
    def executor(self,case,rules,settings):
        return {'complete':True,'evidence_hash':digest(case['evidence']),'metrics':{key:(1 if key=='unsupported_claims' and not rules else 0) for key in METRICS},'elapsed_ms':20,'input_chars':100,'output_chars':100}
    def finish(self,service,payload):
        value=service.start(payload);service.thread.join(3);self.assertIsNone(service.active);return service.get(value['id'])
    def test_real_paired_executor_invocation_and_activation_rollback(self):
        calls=[]
        def executor(case,rules,settings):calls.append((case['split'],rules,digest(case['evidence'])));return self.executor(case,rules,settings)
        service=self.service(executor);candidate=service.create_candidate({'rules':['근거 없는 주장은 미확인으로 표시']})
        run=self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()})
        self.assertEqual(len(calls),4);self.assertEqual(run['status'],'passed')
        for split in ('development','holdout'):self.assertEqual(len({row[2] for row in calls if row[0]==split}),1)
        activated=service.promote(run['id']);self.assertEqual(activated['rules'],candidate['rules'])
        rolled=service.rollback();self.assertEqual(rolled['rules'],[])
        self.assertGreater(rolled['version'],activated['version'])
        with self.assertRaises(ValueError):service.promote(run['id'])
    def test_holdout_regression_blocks_promotion(self):
        def executor(case,rules,settings):
            result=self.executor(case,rules,settings)
            if case['split']=='holdout' and rules:result['metrics']['attribution_errors']=1
            return result
        service=self.service(executor);candidate=service.create_candidate({'rules':['규칙']})
        run=self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()})
        self.assertEqual(run['status'],'rejected')
        with self.assertRaises(ValueError):service.promote(run['id'])
    def test_missing_evaluation_hash_and_reused_holdout_cannot_pass(self):
        service=self.service(self.executor);candidate=service.create_candidate({'rules':['규칙']})
        self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()})
        second=self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()})
        self.assertEqual(second['status'],'rejected');self.assertTrue(second['result']['holdout_reused'])
        service.executor=lambda *args:{'complete':True,'evidence_hash':'wrong','metrics':{key:0 for key in METRICS}}
        third=self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()})
        self.assertEqual(third['status'],'failed')
    def test_same_source_development_holdout_rejected(self):
        service=self.service(self.executor);candidate=service.create_candidate({'rules':['규칙']});cases=self.cases();cases[1]['evidence'][0]['url']=cases[0]['evidence'][0]['url']
        with self.assertRaises(ValueError):service.start({'candidate_id':candidate['id'],'cases':cases})
    def test_active_rules_enter_workflow_input_and_rollback(self):
        from strategic_workflow import WorkflowService,compact_context
        service=self.service(self.executor);candidate=service.create_candidate({'rules':['원문을 벗어난 귀속 금지']})
        run=self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()});service.promote(run['id'])
        workflow=WorkflowService(self.path,sources=object(),enabled=True,extractor=lambda _:[],analyzer=lambda *_:None)
        workflow._start=lambda _:None
        try:
            created=workflow.create_run([{'title':'뉴스','source_url':'https://example.org'}],{})
            with workflow.db() as db:
                import json
                request=json.loads(db.execute('SELECT request_json FROM strategic_workflow_runs WHERE id=?',(created['id'],)).fetchone()[0])
            self.assertEqual(compact_context({'request':request},[])['request']['active_rules']['rules'],candidate['rules'])
            service.rollback()
            second=workflow.create_run([{'title':'뉴스2'}],{})
            with workflow.db() as db:request=json.loads(db.execute('SELECT request_json FROM strategic_workflow_runs WHERE id=?',(second['id'],)).fetchone()[0])
            self.assertEqual(request['active_rules']['rules'],[])
        finally:workflow.close()

    def test_close_finishes_pair_and_resume_preserves_completed_pair(self):
        calls=[]
        def executor(case,rules,settings):
            calls.append(case['id'])
            if len(calls)==1:service.close()
            return self.executor(case,rules,settings)
        service=self.service(executor);candidate=service.create_candidate({'rules':['규칙']})
        run=self.finish(service,{'candidate_id':candidate['id'],'cases':self.cases()})
        self.assertEqual(run['status'],'paused');self.assertEqual(len(calls),2)
        resumed=self.service(lambda case,rules,settings:(calls.append(case['id']) or self.executor(case,rules,settings)))
        resumed.resume(run['id']);resumed.thread.join(3)
        self.assertEqual(resumed.get(run['id'])['status'],'passed')
        self.assertEqual(calls,['development','development','holdout','holdout'])

    def test_real_executor_uses_fixed_evaluator_and_detects_omitted_evidence(self):
        from unittest.mock import patch
        from rule_experiments import execute_case
        case=self.cases()[0];case['evidence'].append(dict(case['evidence'][0],id='unused'))
        report={'claims':[{'text':'관측','category':'observation','evidence_ids':['development'],'uncertainty':'제한'}]}
        audit={key:0 for key in METRICS};audit.update(reviewed_claims=1,checked_evidence_ids=['development'],issues=[])
        with patch('rule_experiments.run_structured',side_effect=[report,audit]) as invoke:
            result=execute_case(case,['SPECIAL_CANDIDATE_RULE'],{})
        self.assertIn('SPECIAL_CANDIDATE_RULE',invoke.call_args_list[0].args[0])
        self.assertNotIn('SPECIAL_CANDIDATE_RULE',invoke.call_args_list[1].args[0])
        self.assertEqual(result['metrics']['missing_citations'],1)
        self.assertIsNone(result['cost'])

    def test_titles_and_legacy_candidate_migration(self):
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE rule_candidates(id TEXT,version INTEGER,rules_json TEXT,created_at TEXT,PRIMARY KEY(id,version))')
            db.execute("INSERT INTO rule_candidates VALUES ('legacy',1,'[]','2000')")
        service=self.service(self.executor)
        first=service.create_candidate({'id':'named','title':'  근거  검토\n규칙  ','rules':['규칙']})
        self.assertEqual(first['title'],'근거 검토 규칙')
        second=service.create_candidate({'id':'named','title':'두번째','rules':['규칙2']})
        self.assertEqual(second['version'],2)
        rows=service.candidates();self.assertEqual(len(rows),3)
        self.assertTrue(next(row for row in rows if row['id']=='legacy')['title'])

    def test_promote_rechecks_frozen_inputs_and_candidate_version(self):
        import json
        for field in ('cases','settings','candidate_rules'):
            with self.subTest(field=field):
                service=self.service(self.executor);candidate=service.create_candidate({'rules':['규칙']})
                cases=self.cases()
                for case in cases:case['evidence'][0]['url']+='?trial='+field
                run=self.finish(service,{'candidate_id':candidate['id'],'cases':cases})
                self.assertEqual(run['status'],'passed')
                with service.db() as db:
                    payload=json.loads(db.execute('SELECT payload_json FROM rule_experiments WHERE id=?',(run['id'],)).fetchone()[0])
                    if field=='cases':payload['cases'][0]['evidence'][0]['text']='변경된 원문'
                    elif field=='settings':payload['settings']['focus']='변경'
                    else:payload['candidate_rules']=['미실험 규칙']
                    db.execute('UPDATE rule_experiments SET payload_json=? WHERE id=?',(json.dumps(payload),run['id']))
                with self.assertRaises(ValueError):service.promote(run['id'])
