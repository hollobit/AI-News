import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from strategic_workflow import WorkflowService, now


class Sources:
    def fetch(self, url):
        return {'status': 'fetched', 'url': url, 'text': '미국 정부는 소버린 AI 인프라 투자 계획을 발표했다.', 'fetched_at': now()}


class Model:
    def __init__(self, reject=False, bad=False, incomplete=False):
        self.calls = []
        self.reject, self.bad, self.incomplete = reject, bad, incomplete

    def __call__(self, prompt, schema):
        role = prompt.splitlines()[0].split(': ')[1]
        self.calls.append(role)
        data = json.loads(prompt.split('DATA:\n')[1])
        ids = [e['id'] for e in data['evidence']]
        if role in ('risk_assessment', 'risk_revision'):
            return {'summary': '선택 원문만으로 현재 위협을 확정하기 어렵습니다.', 'risks': [],
                    'assessed_evidence_ids': ids, 'not_assessable_evidence_ids': [], 'limitations': ['선택 표본']}
        if role in ('verification', 'risk_verification'):
            return {'accepted': not self.reject, 'issues': ['과장'] if self.reject else [],
                    'checked_evidence_ids': [] if self.incomplete else ids, 'limitations': []}
        return {'summary': '정부 인프라 투자 관찰', 'claims': [
            {'title': '소버린 AI', 'detail': '인프라 수요 기회', 'category': 'opportunity',
             'evidence_ids': ['fake'] if self.bad else [ids[0]], 'uncertainty': '계획의 이행 여부 미확인'}], 'limitations': ['선택 표본']}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'news.db'
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close()
        self.temp.cleanup()

    def service(self, model=None, **kw):
        service = WorkflowService(self.path, sources=kw.pop('sources', Sources()), analyzer=model or Model(),
                                  extractor=lambda text: [{'label': '소버린 AI', 'kind': 'technical_dictionary'}], enabled=True, **kw)
        self.services.append(service)
        return service

    def items(self, count=1):
        return [{'title': f'정부 소버린 AI 투자 {i}', 'source_url': f'https://example.com/{i}', 'published_at': '2026-09-15'} for i in range(count)]

    def done(self, service, run_id):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            run = service.get_run(run_id)
            if run['status'] in ('complete', 'needs_review', 'failed', 'paused') and service.active is None:
                return run
            time.sleep(.01)
        self.fail('workflow timeout')

    def test_role_sequence_persistence_and_bounds(self):
        model = Model()
        service = self.service(model)
        run = self.done(service, service.create_run(self.items(30), {'question': '소버린 AI'})['id'])
        self.assertEqual(run['status'], 'complete', run['error'])
        self.assertEqual(run['snapshot_count'], 24)
        self.assertEqual(set(model.calls[:3]), {'national', 'technology', 'risk_assessment'})
        self.assertEqual(set(model.calls[3:5]), {'synthesis', 'risk_verification'})
        self.assertEqual(model.calls[5:], ['verification'])
        self.assertEqual(run['stages']['revision'], 'skipped')
        self.assertTrue(run['results']['verified'])
        self.assertTrue(run['results']['risk_verified'])
        self.assertFalse(run['results']['orchestration']['mirofish_simulation_executed'])
        service.close()
        reopened = self.service()
        self.assertEqual(reopened.get_run(run['id'])['results'], run['results'])

    def test_high_impact_value_is_snapshot_metadata_not_truth(self):
        from strategic_value import evaluate_news
        item = {'title': '정부 국가안보와 반도체 수출통제 정책', 'summary': '교육과 산업에 미치는 조건부 영향'}
        service = self.service()
        run = self.done(service, service.create_run([item])['id'])
        self.assertEqual(run['results']['evidence'][0]['strategic_value'], evaluate_news(item))
        for role in ('national', 'technology', 'synthesis', 'verification'):
            prompt = service._prompt(role, run['results']['evidence'], {})
            self.assertIn('국가경제·국가안보·산업·수출·사회문제·생활·교육', prompt)
            self.assertIn('구체적 영향 경로', prompt)
            self.assertIn('높은 진실성·확정적 인과성', prompt)

    def test_ai_control_monitoring_distinguishes_actions_and_proposals(self):
        service = self.service()
        for role in ('national', 'technology', 'synthesis', 'revision', 'verification'):
            prompt = service._prompt(role, [], {})
            for term in ('shutdown', 'training pause', 'compute limits', 'deployment suspension', 'learning rate'):
                self.assertIn(term, prompt)
            self.assertIn('정책적 AI 개발 속도 제한과 혼동하지 말 것', prompt)
            self.assertIn('detail에 관측 또는 제안으로 명시', prompt)
            self.assertIn('명시적 원문 근거와 인용', prompt)

    def test_risk_schema_rules_do_not_leak_into_strategy_review(self):
        service = self.service()
        for role in ('national', 'technology', 'synthesis', 'revision', 'verification'):
            prompt = service._prompt(role, [], {})
            self.assertNotIn('assessed_evidence_ids', prompt)
            self.assertNotIn('각 설명은 120자', prompt)
            self.assertIn('summary/claims/limitations', prompt)
        for role in ('risk_assessment', 'risk_revision', 'risk_verification'):
            prompt = service._prompt(role, [], {})
            self.assertIn('assessed_evidence_ids', prompt)
            self.assertIn('각 설명은 120자', prompt)

    def test_bad_citation_cannot_be_verified(self):
        service = self.service(Model(bad=True))
        run = self.done(service, service.create_run(self.items())['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertIsNone(run['results'])
        self.assertIn('근거', run['error'])

    def test_rejection_only_one_repair_and_needs_review(self):
        model = Model(reject=True)
        service = self.service(model)
        run = self.done(service, service.create_run(self.items())['id'])
        self.assertEqual(run['status'], 'needs_review')
        self.assertEqual(model.calls.count('revision'), 1)
        self.assertEqual(model.calls.count('verification'), 2)
        self.assertFalse(run['results']['verified'])
        with self.assertRaises(ValueError):
            service.resume(run['id'])

    def test_incomplete_audit_is_rejected(self):
        service = self.service(Model(incomplete=True))
        run = self.done(service, service.create_run(self.items())['id'])
        self.assertEqual(run['status'], 'needs_review')
        self.assertIn('대조', run['results']['verification']['issues'][0])

    def test_risk_rejection_blocks_overall_verification_and_repairs_once(self):
        model = Model()
        def risk_reject(prompt, schema):
            result = model(prompt, schema)
            if prompt.startswith('ROLE: risk_verification'):
                result.update(accepted=False, issues=['현재 위협과 미래 가능성 구분 불충분'])
            return result
        service = self.service(risk_reject)
        run = self.done(service, service.create_run(self.items())['id'])
        self.assertEqual(run['status'], 'needs_review', run['error'])
        self.assertTrue(run['results']['verification']['accepted'])
        self.assertFalse(run['results']['risk_verified'])
        self.assertFalse(run['results']['verified'])
        self.assertEqual(model.calls.count('risk_revision'), 1)
        self.assertEqual(model.calls.count('risk_verification'), 2)

    def test_missing_source_dates_explicit(self):
        class Undated:
            def fetch(self, url):
                return {'status': 'fetched', 'text': '정부 투자', 'url': url}
        service = self.service(sources=Undated())
        run = self.done(service, service.create_run(self.items())['id'])
        self.assertEqual(run['coverage']['stale_or_undated_urls'], ['https://example.com/0'])

    def test_audit_cannot_validate_changed_evidence_or_report(self):
        service = self.service()
        evidence = [{'id': 'news_one', 'text': '정부 투자 계획'}]
        report = Model()('ROLE: synthesis\nDATA:\n' + json.dumps({'evidence': evidence}), {})
        audit = service._audit(evidence, report)
        self.assertTrue(service._current_audit(audit, evidence, report)['accepted'])
        self.assertFalse(service._current_audit(audit, [dict(evidence[0], text='계획 철회')], report)['accepted'])
        self.assertFalse(service._current_audit(audit, evidence, dict(report, summary='완료 확정'))['accepted'])

    def test_rejected_audit_reobserves_only_failed_original_urls_once(self):
        class RecoveringSources:
            def __init__(self):
                self.calls = []

            def fetch(self, url, refresh=False):
                self.calls.append((url, refresh))
                if url.endswith('/0') and not refresh:
                    return {'status': 'failed', 'text': '', 'url': url}
                return {'status': 'fetched', 'text': '소버린 AI 정부 투자 추가 근거', 'url': url, 'fetched_at': now()}

        model, sources = Model(), RecoveringSources()
        observations = []

        def review(prompt, schema):
            data = json.loads(prompt.split('DATA:\n')[1])
            observations.append((prompt.splitlines()[0], data['evidence']))
            result = model(prompt, schema)
            if prompt.startswith('ROLE: verification') and model.calls.count('verification') == 1:
                result.update(accepted=False, issues=['원문이 누락되어 추가 근거가 필요합니다.'])
            return result

        service = self.service(review, sources=sources)
        run = self.done(service, service.create_run(self.items(2))['id'])
        self.assertEqual(run['status'], 'complete', run['error'])
        self.assertEqual(sources.calls.count(('https://example.com/0', True)), 1)
        self.assertNotIn(('https://example.com/1', True), sources.calls)
        self.assertEqual(run['coverage']['failed_urls'], [])
        self.assertEqual(run['coverage']['fetched_urls'], 2)
        audits = [ev for role, ev in observations if role == 'ROLE: verification']
        self.assertEqual([len(ev) for ev in audits], [3, 4])
        self.assertEqual(model.calls.count('revision'), 1)
        self.assertNotEqual(run['artifacts']['verification']['evidence_hash'], run['results']['verification']['evidence_hash'])
        self.assertTrue(service._current_audit(run['results']['verification'], run['results']['evidence'], run['results']['report'])['accepted'])

    def test_close_pauses_and_resume_reuses_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        model = Model()
        def blocking(prompt, schema):
            entered.set()
            release.wait(2)
            return model(prompt, schema)
        service = self.service(blocking)
        run = service.create_run(self.items())
        self.assertTrue(entered.wait(3))
        with self.assertRaises(RuntimeError):
            service.create_run(self.items())
        service.close()
        release.set()
        paused = self.done(service, run['id'])
        self.assertEqual(paused['status'], 'paused')
        reopened = self.service()
        finished = self.done(reopened, reopened.resume(run['id'])['id'])
        self.assertEqual(finished['status'], 'complete', finished['error'])
        self.assertEqual(finished['snapshot_count'], 1)
        self.assertEqual(sum(e['stage'] == 'enrichment' and e['status'] == 'complete' for e in finished['events']), 1)


class CompletionWorkflowTests(unittest.TestCase):
    setUp=WorkflowTests.setUp
    tearDown=WorkflowTests.tearDown
    service=WorkflowTests.service
    items=WorkflowTests.items
    done=WorkflowTests.done

    def test_first_three_roles_actually_overlap(self):
        barrier=threading.Barrier(3);model=Model()
        def analyzer(prompt,schema):
            role=prompt.splitlines()[0].split(': ')[1]
            if role in ('national','technology','risk_assessment'):barrier.wait(timeout=2)
            return model(prompt,schema)
        service=self.service(analyzer)
        run=self.done(service,service.create_run(self.items(),{})['id'])
        self.assertEqual(run['status'],'complete',run['error'])

    def test_full_corpus_24_news_require_all_citations(self):
        model=Model()
        def analyzer(prompt,schema):
            value=model(prompt,schema)
            role=prompt.splitlines()[0].split(': ')[1]
            if role in ('synthesis','revision'):
                self.assertIn('최대 32개',prompt)
                data=json.loads(prompt.split('DATA:\n')[1])
                value['claims']=[dict(value['claims'][0],evidence_ids=[entry['id']]) for entry in data['evidence'] if entry['origin']=='telegram_excerpt']
            return value
        service=self.service(analyzer)
        run=self.done(service,service.create_run(self.items(24),{'completion':True})['id'])
        self.assertEqual(run['status'],'complete',run['error'])
        self.assertEqual(len(run['results']['report']['claims']),24)
        service2=self.service(Model())
        missing=self.done(service2,service2.create_run(self.items(2),{'full_corpus':True})['id'])
        self.assertEqual(missing['status'],'needs_review')
        self.assertTrue(any('누락' in issue for issue in missing['results']['verification']['issues']))

    def test_compact_keeps_original_evidence_and_removes_heavy_hints(self):
        from strategic_workflow import compact_context
        evidence=[{'id':'e1','text':'온전한 원문','origin':'telegram_excerpt'}]
        context={'keywords':[{'evidence_id':'e1','keywords':[{'id':str(i),'label':'온전한 용어','surface':'온전한 용어','pos':['NNG'],'unneeded':'x'*1000} for i in range(40)]}],
                 'graph_context':{'result':{'nodes':[{'id':'n','name':'단서','summary':'x'*10000,'large_metadata':['y'*10000]}]*24}},
                 'request':{'completion':True,'improvement_context':{'followup_tasks':[{'evidence_ids':['other'],'text':'irrelevant'}]}}}
        service=self.service()
        prompt=service._prompt('synthesis',evidence,context)
        data=json.loads(prompt.split('DATA:\n')[1])
        self.assertEqual(data['evidence'],evidence)
        self.assertNotIn('keywords',data['context'])
        self.assertEqual(len(compact_context(context,evidence)['keywords'][0]['keyword_pairs']),8)
        self.assertNotIn('large_metadata',prompt)
        self.assertNotIn('irrelevant',prompt)
        self.assertEqual(context['keywords'][0]['keywords'][0]['unneeded'],'x'*1000)
        self.assertLess(len(prompt),10000)

    def test_risk_generation_requires_basis_even_for_moderate(self):
        prompt=self.service()._prompt('risk_assessment',[],{})
        for phrase in ['low/moderate를 포함한 모든','current_severity=unknown','future_likelihood=unknown',
                       '분석가가 제안한 관찰 기간','정보 부재나 독립 확인 부족은 반대 근거가 아니므로','조달·예산·접근 제한 정책']:
            self.assertIn(phrase,prompt)

    def test_article_date_and_telegram_timestamp_are_separate(self):
        item=dict(self.items()[0],day='2025-01-01',date_basis='article',published_at='2026-09-15T10:00:00Z')
        service=self.service()
        run=self.done(service,service.create_run([item],{})['id'])
        with service.db() as db:snapshot=json.loads(db.execute('SELECT snapshot_json FROM strategic_workflow_runs WHERE id=?',(run['id'],)).fetchone()[0])
        self.assertEqual(snapshot[0]['article_date'],'2025-01-01')
        self.assertEqual(snapshot[0]['published_at'],'2026-09-15T10:00:00Z')
        self.assertEqual(snapshot[0]['telegram_published_at'],'2026-09-15T10:00:00Z')
        self.assertIn('조회 시각',service._prompt('national',snapshot,{}))
        run=self.done(service,service.create_run([dict(item,date_basis='telegram')],{})['id'])
        self.assertEqual(run['results']['evidence'][0]['article_date'],'')

    def test_owner_recovery_never_pauses_live_or_unknown_owners(self):
        import os
        service=self.service()
        with service.db() as db:
            for identity,request in [('live',{'owner_pid':os.getpid()}),('dead',{'owner_pid':999999999}),('legacy',{})]:
                db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?,?,?)',(identity,'running',now(),now(),'[]',json.dumps(request),''))
        self.service(recover_interrupted=False)
        with service.db() as db:self.assertEqual(db.execute("SELECT status FROM strategic_workflow_runs WHERE id='dead'").fetchone()[0],'running')
        self.service()
        with service.db() as db:
            statuses=dict(db.execute('SELECT id,status FROM strategic_workflow_runs'))
        self.assertEqual(statuses,{'live':'running','dead':'paused','legacy':'running'})


class EvidenceSchemaTests(unittest.TestCase):
    def test_all_reference_arrays_bound_and_shared_schemas_unchanged(self):
        import copy
        from concurrent.futures import ThreadPoolExecutor
        from strategic_workflow import REPORT,AUDIT,evidence_schema
        from risk_analysis import RISK_SCHEMA
        originals=[REPORT,AUDIT,RISK_SCHEMA];before=copy.deepcopy(originals)
        def bind(identity):return [evidence_schema(schema,[{'id':identity}]) for schema in originals]
        with ThreadPoolExecutor(max_workers=2) as pool:
            first,second=list(pool.map(bind,['a','b']))
        def enums(node):
            result=[]
            if isinstance(node,dict):
                for key,value in node.get('properties',{}).items():
                    if key in ('evidence_ids','checked_evidence_ids','assessed_evidence_ids','not_assessable_evidence_ids'):
                        result.append(value['items']['enum'])
                for value in node.values():result.extend(enums(value))
            elif isinstance(node,list):
                for value in node:result.extend(enums(value))
            return result
        self.assertEqual(enums(first),[['a']]*5)
        self.assertEqual(enums(second),[['b']]*5)
        first[0]['properties']['claims']['items']['properties']['evidence_ids']['items']['enum'].append('mutated')
        self.assertEqual(enums(second),[['b']]*5)
        self.assertEqual(originals,before)
        with self.assertRaises(ValueError):evidence_schema(REPORT,[])

    def test_every_role_receives_only_actual_request_ids(self):
        case=WorkflowTests();case.setUp()
        model=Model(reject=True)
        def analyzer(prompt,schema):
            from strategic_workflow import evidence_schema
            data=json.loads(prompt.split('DATA:\n')[1])
            expected=sorted(entry['id'] for entry in data['evidence'])
            def check(node):
                if isinstance(node,dict):
                    if 'enum' in node and node.get('type')=='string' and any(value.startswith(('news_','url_')) for value in node['enum']):
                        self.assertEqual(node['enum'],expected)
                    for value in node.values():check(value)
                elif isinstance(node,list):
                    for value in node:check(value)
            check(schema)
            return model(prompt,schema)
        try:
            service=case.service(analyzer)
            run=case.done(service,service.create_run(case.items(),{})['id'])
            self.assertEqual(run['status'],'needs_review')
            self.assertIn('revision',model.calls)
            self.assertIn('risk_revision',model.calls)
        finally:case.tearDown()


class RiskReuseTests(unittest.TestCase):
    setUp=WorkflowTests.setUp
    tearDown=WorkflowTests.tearDown
    service=WorkflowTests.service
    items=WorkflowTests.items
    done=WorkflowTests.done
    def test_unchanged_accepted_risk_is_reused_during_main_repair(self):
        model=Model()
        def analyzer(prompt,schema):
            response=model(prompt,schema)
            if prompt.startswith('ROLE: verification'):
                response.update(accepted=False,issues=['과장'])
            return response
        service=self.service(analyzer)
        result=self.done(service,service.create_run(self.items(),{})['id'])
        self.assertEqual(model.calls.count('risk_verification'),1)
        self.assertTrue(result['results']['risk_verification']['reused'])

    def test_risk_only_rejection_reuses_accepted_strategy_without_calls(self):
        model=Model()
        def analyzer(prompt,schema):
            response=model(prompt,schema)
            if prompt.startswith('ROLE: risk_verification'):
                response.update(accepted=False,issues=['위험 지적'])
            return response
        service=self.service(analyzer)
        run=self.done(service,service.create_run(self.items(),{'completion':True})['id'])
        self.assertEqual(run['status'],'needs_review')
        self.assertTrue(run['results']['verification']['reused'])
        self.assertEqual(model.calls.count('revision'),0)
        self.assertEqual(model.calls.count('verification'),1)
        self.assertEqual(model.calls.count('risk_revision'),1)

    def test_fresh_source_observation_forces_strategy_reverification(self):
        model=Model()
        class RepairedSource:
            def fetch(self,url,refresh=False):
                return {'status':'fetched','url':url,'text':'보강된 AI 원문','fetched_at':now()} if refresh else {'status':'failed','url':url,'text':''}
        def analyzer(prompt,schema):
            response=model(prompt,schema)
            if prompt.startswith('ROLE: risk_verification'):response.update(accepted=False,issues=['위험 지적'])
            return response
        service=self.service(analyzer,sources=RepairedSource())
        run=self.done(service,service.create_run(self.items(),{})['id'])
        self.assertEqual(model.calls.count('revision'),1)
        self.assertEqual(model.calls.count('verification'),2)
        self.assertFalse(run['results']['verification'].get('reused',False))

    def test_completion_checks_unassessable_evidence_without_promoting_it(self):
        model=Model()
        service=self.service(model)
        evidence=[{'id':'a','origin':'telegram_excerpt','text':'발췌'}]
        report={'summary':'평가 불가','risks':[],'assessed_evidence_ids':[],
                'not_assessable_evidence_ids':['a'],'limitations':['정보 부족']}
        audit=service._audit(evidence,report,risk=True,require_all_news=True)
        self.assertTrue(audit['accepted'])
        self.assertEqual(report['assessed_evidence_ids'],[])
        self.assertFalse(service._reusable_risk_audit(dict(audit,checked_evidence_ids=[]),evidence,report,True))
        incomplete=self.service(Model(incomplete=True))
        self.assertTrue(incomplete._audit(evidence,report,risk=True)['accepted'])
        self.assertFalse(incomplete._audit(evidence,report,risk=True,require_all_news=True)['accepted'])
        self.assertIn('not_assessable로 분류한 모든 근거도',service._prompt('risk_verification',evidence,{'request':{'completion':True}}))

    def test_strategy_reuse_requires_full_coverage_hash_version_and_checked_refs(self):
        from strategic_workflow import digest
        service=self.service()
        evidence=[{'id':'a','origin':'telegram_excerpt','text':'A'},{'id':'b','origin':'telegram_excerpt','text':'B'}]
        report={'summary':'요약','claims':[{'title':'A','detail':'관측','category':'watch_signal','uncertainty':'미확인','evidence_ids':['a']}],'limitations':[]}
        audit={'accepted':True,'issues':[],'checked_evidence_ids':['a'],'verification_version':'grounded-strategy-v2',
               'report_hash':digest(report),'evidence_hash':digest(evidence)}
        self.assertTrue(service._reusable_strategy_audit(audit,evidence,report))
        self.assertFalse(service._reusable_strategy_audit(audit,evidence,report,True))
        for key,value in [('report_hash','old'),('evidence_hash','old'),('verification_version','old'),('checked_evidence_ids',[]),('checked_evidence_ids',['a','unknown']),('issues',['unresolved'])]:
            self.assertFalse(service._reusable_strategy_audit(dict(audit,**{key:value}),evidence,report))
        self.assertFalse(service._reusable_strategy_audit(audit,[dict(evidence[0],text='changed'),evidence[1]],report))

    def test_reuse_requires_hash_version_and_complete_checks(self):
        from strategic_workflow import digest
        service=self.service()
        evidence=[{'id':'a','text':'original'}]
        report={'risks':[],'assessed_evidence_ids':['a']}
        audit={'accepted':True,'issues':[],'checked_evidence_ids':['a'],
               'verification_version':'grounded-risk-v2','report_hash':digest(report),'evidence_hash':digest(evidence)}
        self.assertTrue(service._reusable_risk_audit(audit,evidence,report))
        for field,value in [('verification_version','old'),('checked_evidence_ids',[]),('accepted',False),('issues',['bad']),('report_hash','changed'),('evidence_hash','changed')]:
            self.assertFalse(service._reusable_risk_audit(dict(audit,**{field:value}),evidence,report))
        self.assertIn('처리 메타데이터',service._prompt('synthesis',evidence,{}))


if __name__ == '__main__':
    unittest.main()

    def test_material_role_difference_must_survive_synthesis_and_review(self):
        class DifferenceModel(Model):
            def __call__(self,prompt,schema):
                result=super().__call__(prompt,schema);role=prompt.splitlines()[0].split(': ')[1]
                if role=='technology':result['claims'][0]['detail']='기술 비용의 불확실성'
                if role=='verification':
                    questions=json.loads(prompt.split('DATA:\n')[1])['context']['deliberation']['questions']
                    result['deliberation_checks']=[{'difference_id':question['id'],'material':True,'classification':'different_scope','preserved':False,'reason':'중요 입장 누락','opposing_evidence_ids':[],'change_conditions':[]} for question in questions]
                return result
        model=DifferenceModel();service=self.service(model)
        run=self.done(service,service.create_run(self.items())['id'])
        self.assertEqual(run['status'],'needs_review',run['error'])
        self.assertEqual(model.calls.count('verification'),2)
        self.assertTrue(run['results']['deliberation']['questions'])
        audit=run['results']['verification'];audit=dict(audit,accepted=True,deliberation_checks=[])
        current=service._current_audit(audit,run['results']['evidence'],run['results']['report'],deliberation=run['results']['deliberation'])
        self.assertFalse(current['accepted'])

    def test_event_extraction_uses_existing_synthesis_and_independent_audit(self):
        class EventsModel(Model):
            def __call__(self,prompt,schema):
                result=super().__call__(prompt,schema);role=prompt.splitlines()[0].split(': ')[1]
                data=json.loads(prompt.split('DATA:\n')[1])
                if role in ('synthesis','revision'):
                    result['event_observations']=[{'actor':'정부','action':'투자 계획 발표','target':'소버린 AI','outcome':'','occurred_at':'','actor_countries':[],'affected_countries':[],'evidence_ids':[data['evidence'][0]['id']],'uncertainty':'발표 계획의 실현 미확인'}]
                if role=='verification':result['checked_event_indices']=[0]
                return result
        model=EventsModel();service=self.service(model)
        run=self.done(service,service.create_run(self.items())['id'])
        self.assertEqual(run['status'],'complete',run['error'])
        self.assertEqual(len(model.calls),6)
        events=run['results']['event_observations']
        self.assertEqual(events,run['results']['report']['event_observations'])
        from strategic_workflow import digest
        self.assertEqual(run['results']['verification']['event_hash'],digest(events))
