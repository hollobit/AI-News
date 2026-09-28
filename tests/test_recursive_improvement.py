import tempfile
import time
import unittest
from pathlib import Path

from recursive_improvement import RecursiveImprovementService, fingerprint, news_fingerprint, news_identity


class FakeWorkflow:
    enabled = True
    active = None

    def __init__(self, reject=False, hold=False):
        self.runs = {}
        self.requests = []
        self.reject = reject
        self.hold = hold
        self.closed = False

    def create_run(self, items, request):
        identity = str(len(self.runs)+1)
        self.requests.append(request)
        evidence = [{'id': 'e'+str(i), 'text': '\n'.join(str(item.get(k) or '') for k in ('title','summary','text','description')).strip()[:2000],
                     'title': item.get('title',''), 'url': item.get('source_url',''), 'origin': 'telegram_excerpt'} for i,item in enumerate(items)]
        report = {'summary': '범위 한정 결과', 'claims': [{'title': '소버린 AI', 'detail': '정부 인프라 투자 제안', 'category': 'strategic_concept',
                                                       'uncertainty': '시행 확인 필요', 'evidence_ids': [e['id'] for e in evidence]}], 'limitations': []}
        audit = {'accepted': not self.reject, 'issues': ['추가 원문 근거 필요'] if self.reject else [],
                 'checked_evidence_ids': [e['id'] for e in evidence], 'report_hash': fingerprint(report), 'evidence_hash': fingerprint(evidence)}
        result = {'report': report, 'evidence': evidence, 'verification': audit, 'verified': not self.reject,
                  'coverage': {'requested_urls': 0, 'fetched_urls': 0, 'failed_urls': []}}
        run = {'id': identity, 'status': 'running' if self.hold else 'needs_review' if self.reject else 'complete',
               'results': result, 'request': request, 'error': ''}
        self.runs[identity] = run
        return run

    def get_run(self, identity):
        return self.runs.get(identity)

    def resume(self, identity):
        self.runs[identity]['status'] = 'complete'
        return self.runs[identity]


class Batch(list):
    @property
    def coverage(self):
        return {'total_unique': len(self), 'duplicates_excluded': 2}


def catalog(db, run_id, workflow):
    return {'accepted': workflow['status'] == 'complete', 'added': [], 'updated': [], 'followup_tasks': [], 'catalog_version': 0}


class RecursiveImprovementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'news.db'
        self.services = []
        self.items = [{'id': i, 'title': f'정부 소버린 AI {i}', 'text': '정부 인프라 투자', 'source_url': f'https://example.com/{i}'} for i in range(3)]

    def tearDown(self):
        for service in self.services:
            service.close()
            if service.thread:
                service.thread.join(3)
        self.temp.cleanup()

    def service(self, workflow=None):
        workflow = workflow or FakeWorkflow()
        service = RecursiveImprovementService(self.path, workflow,
                    lambda settings,tasks,seen: Batch(sorted(self.items, key=lambda i: news_identity(i) in seen)), catalog_updater=catalog)
        self.services.append(service)
        return service

    def until(self, service, identity, predicate, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            cycle = service.get(identity)
            if predicate(cycle):
                return cycle
            time.sleep(.01)
        self.fail(str(service.get(identity)))

    def fast_forward(self, service, cycle_id):
        service._update(cycle_id, next_run_at='2000-01-01T00:00:00+00:00')
        service.wake.set()

    def test_full_corpus_coverage_and_no_identical_reanalysis(self):
        service = self.service()
        cycle = service.start({'max_news': 2, 'max_rounds': 0})
        first = self.until(service, cycle['id'], lambda c:c['metrics']['processed_unique']==2)
        self.assertEqual(first['metrics']['remaining'],1)
        self.fast_forward(service, cycle['id'])
        done = self.until(service, cycle['id'], lambda c:c['metrics']['all_verified'])
        self.assertEqual(done['metrics']['processed_unique'],3)
        self.assertEqual(len(service.workflow.requests),2)
        self.fast_forward(service, cycle['id'])
        waiting = self.until(service, cycle['id'], lambda c:c['metrics']['no_progress_rounds']>0)
        self.assertEqual(waiting['status'],'waiting')
        self.assertEqual(len(service.workflow.requests),2)
        self.assertIn('improvement_context',service.workflow.requests[1])

    def test_rejected_snapshot_only_one_feedback_retry_then_wait(self):
        self.items = self.items[:1]
        service = self.service(FakeWorkflow(reject=True))
        cycle = service.start({'max_rounds': 0})
        first = self.until(service,cycle['id'],lambda c:len(c['rounds'])==1 and c['rounds'][0]['status']=='needs_review')
        self.assertTrue(first['rules'])
        self.fast_forward(service,cycle['id'])
        self.until(service,cycle['id'],lambda c:len(c['rounds'])==2 and c['rounds'][1]['status']=='needs_review')
        self.fast_forward(service,cycle['id'])
        waiting = self.until(service,cycle['id'],lambda c:c['metrics']['no_progress_rounds']>=2)
        self.assertEqual(len(service.workflow.requests),2)
        self.assertEqual(waiting['metrics']['needs_review_unique'],1)
        self.assertEqual(waiting['metrics']['verified_unique'],0)
        self.assertTrue(service.workflow.requests[1]['improvement_context']['rule_proposals'])
        self.assertNotIn('rules',service.workflow.requests[1]['improvement_context'])

    def test_pause_finishes_inflight_and_resume_does_not_duplicate(self):
        workflow = FakeWorkflow(hold=True)
        service = self.service(workflow)
        cycle = service.start({'max_news':1})
        self.until(service,cycle['id'],lambda c:c['rounds'] and c['rounds'][0]['workflow_run_id'])
        self.assertEqual(service.pause(cycle['id'])['status'],'finishing')
        workflow.runs['1']['status']='complete'
        service.wake.set()
        self.until(service,cycle['id'],lambda c:c['status']=='paused')
        service.thread.join(3)
        workflow.hold=False
        service.resume(cycle['id'])
        self.until(service,cycle['id'],lambda c:c['metrics']['processed_unique']>=2)
        self.assertEqual(len(workflow.requests),2)

    def test_changed_article_is_new_observation_but_fetch_timestamp_is_not(self):
        item = dict(self.items[0], source_context={'status':'fetched','text':'article','fetched_at':'yesterday'})
        other = dict(item, source_context={'status':'fetched','text':'article','fetched_at':'today'})
        self.assertEqual(news_fingerprint(item),news_fingerprint(other))
        other['source_context']['text']='changed'
        self.assertNotEqual(news_fingerprint(item),news_fingerprint(other))

    def test_disabled_rejects_before_persistent_cycle(self):
        workflow=FakeWorkflow();workflow.enabled=False
        service=self.service(workflow)
        with self.assertRaises(RuntimeError):
            service.start()
        self.assertEqual(service.list(),[])

    def test_budget_exhaustion_does_not_claim_all_news_complete(self):
        service=self.service()
        cycle=service.start({'max_news':1,'max_rounds':1})
        done=self.until(service,cycle['id'],lambda c:c['status']=='budget_exhausted')
        self.assertEqual(done['metrics']['remaining'],2)
        self.assertFalse(done['metrics']['all_processed'])

    def test_legacy_processed_scope_gets_one_risk_backfill_before_new_news(self):
        service=self.service()
        cycle=service.start({'max_news':1,'max_rounds':0})
        self.until(service,cycle['id'],lambda c:c['metrics']['processed_unique']==1)
        service.pause(cycle['id'])
        service.thread.join(3)
        state=service._state(cycle['id'])
        state['https://example.com/0'].pop('risk_attempted',None)
        state['https://example.com/0'].pop('risk_assessed',None)
        from recursive_improvement import encoded
        service._update(cycle['id'],state_json=encoded(state))
        service.resume(cycle['id'])
        result=self.until(service,cycle['id'],lambda c:len(c['rounds'])>=2 and c['rounds'][1]['status']=='complete')
        self.assertTrue(result['rounds'][1]['snapshot']['risk_backfill'])
        self.assertEqual(result['rounds'][1]['snapshot_ids'],['https://example.com/0'])
        self.assertEqual(result['metrics']['processed_unique'],1)
        self.assertEqual(result['metrics']['risk_assessed_unique'],0)
        self.fast_forward(service,cycle['id'])
        advanced=self.until(service,cycle['id'],lambda c:c['metrics']['processed_unique']==2)
        self.assertEqual(advanced['rounds'][2]['snapshot_ids'],['https://example.com/1'])


if __name__=='__main__':
    unittest.main()
