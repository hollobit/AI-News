"""Independent regressions for honest archive coverage and stale-result reuse."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from improvement_selection import SelectionBatch
from recursive_improvement import RecursiveImprovementService, fingerprint, news_identity, quality_metrics


def signed_run(run_id='old', evidence=None):
    evidence = evidence or [{'id': 'e1', 'url': 'https://example.com/a', 'origin': 'telegram_excerpt',
                             'title': '원문', 'text': '원문\n\n소버린 AI'}]
    report = {'summary': '전략 검토', 'claims': [{'title': '소버린 AI 전략', 'detail': '원문을 검토했다.',
              'category': 'watch_signal', 'uncertainty': '부분 발췌', 'evidence_ids': ['e1']}], 'limitations': []}
    return {'id': run_id, 'status': 'complete', 'error': '', 'results': {
        'verified': True, 'report': report, 'evidence': evidence, 'coverage': {},
        'verification': {'accepted': True, 'issues': [], 'checked_evidence_ids': ['e1'],
                         'report_hash': fingerprint(report), 'evidence_hash': fingerprint(evidence)}}}


class ImprovementCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'news.sqlite3'
        self.items = [{'id': 'a', 'title': '원문', 'text': '소버린 AI', 'source_url': 'https://example.com/a'},
                      {'id': 'b', 'title': '다른 원문', 'text': 'Physical AI', 'source_url': 'https://example.com/b'}]
        self.runs = {}
        self.workflow = SimpleNamespace(active=None, get_run=lambda identity: self.runs.get(identity))
        self.service = RecursiveImprovementService(self.path, self.workflow,
            lambda settings, tasks, seen: SelectionBatch(self.items, {'total_unique': len(self.items)}),
            catalog_updater=lambda db, identity, run: {'accepted': quality_metrics(run)['verified'], 'followup_tasks': []})

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def cycle(self, **settings):
        with patch.object(self.service, '_launch'):
            return self.service.start({'max_news': 2, 'require_risk': False, **settings})

    def seed(self, run):
        self.runs[run['id']] = run
        with self.service.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS strategic_workflow_runs(id TEXT,status TEXT,snapshot_json TEXT,request_json TEXT,created_at TEXT)')
            db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?)',
                       (run['id'], run['status'], json.dumps(run['results']['evidence']), '{}', '2026-09-15'))

    def test_quality_rejects_error_or_simulated_or_empty_source(self):
        self.assertTrue(quality_metrics(signed_run())['verified'])
        for mutation in ('error', 'simulation', 'empty'):
            run = signed_run()
            if mutation == 'error':
                run['error'] = '분석 오류'
            else:
                run['results']['evidence'][0].update({'origin': 'mirofish_simulation'} if mutation == 'simulation' else {'text': ''})
                run['results']['verification']['evidence_hash'] = fingerprint(run['results']['evidence'])
            with self.subTest(mutation=mutation):
                self.assertFalse(quality_metrics(run)['verified'])

    def test_seed_never_marks_uncited_evidence_verified(self):
        evidence = signed_run()['results']['evidence'] + [{'id': 'e2', 'url': 'https://example.com/b',
                    'text': 'Physical AI', 'origin': 'telegram_excerpt'}]
        self.seed(signed_run(evidence=evidence))
        cycle = self.cycle()
        state = self.service._state(cycle['id'])
        self.assertNotEqual(state.get('https://example.com/b', {}).get('status'), 'verified')

    def test_unchanged_verified_seed_is_reused(self):
        self.seed(signed_run())
        self.items = self.items[:1]
        cycle = self.cycle()
        self.service._plan_round(cycle)
        current = self.service.get(cycle['id'])
        self.assertEqual(current['rounds'], [])
        self.assertEqual(current['metrics']['verified_unique'], 1)

    def test_mixed_real_and_simulated_claims_do_not_pass_whole_report(self):
        run = signed_run()
        run['results']['evidence'].append({'id': 'sim', 'url': 'https://example.com/b',
            'origin': 'mirofish_simulation', 'text': '가상 에이전트 발언'})
        run['results']['report']['claims'].append(dict(run['results']['report']['claims'][0],
            title='가상발언 기반 주장', evidence_ids=['sim']))
        run['results']['verification'].update(checked_evidence_ids=['e1', 'sim'],
            report_hash=fingerprint(run['results']['report']), evidence_hash=fingerprint(run['results']['evidence']))
        self.assertFalse(quality_metrics(run)['verified'])

    def test_uncited_fetched_excerpt_cannot_be_reused_as_verified_source(self):
        run = signed_run()
        run['results']['evidence'].append({'id': 'url1', 'url': 'https://example.com/a',
            'origin': 'fetched_url_excerpt', 'text': '검증에 쓰이지 않은 원문'})
        run['results']['verification']['evidence_hash'] = fingerprint(run['results']['evidence'])
        self.seed(run)
        self.items = [dict(self.items[0], source_context={'status': 'fetched', 'text': '검증에 쓰이지 않은 원문'})]
        cycle = self.cycle()
        self.service._plan_round(cycle)
        self.assertEqual(len(self.service.get(cycle['id'])['rounds']), 1)

    def test_changed_seed_source_is_queued_again(self):
        run = signed_run()
        run['results']['evidence'].append({'id': 'url1', 'url': 'https://example.com/a',
            'origin': 'fetched_url_excerpt', 'text': '이전 원문'})
        run['results']['report']['claims'][0]['evidence_ids'].append('url1')
        run['results']['verification'].update(checked_evidence_ids=['e1', 'url1'],
            report_hash=fingerprint(run['results']['report']), evidence_hash=fingerprint(run['results']['evidence']))
        self.seed(run)
        self.items = [dict(self.items[0], source_context={'status': 'fetched', 'text': '수정된 새로운 원문'})]
        cycle = self.cycle()
        self.service._plan_round(cycle)
        current = self.service.get(cycle['id'])
        self.assertEqual(len(current['rounds']), 1)
        self.assertEqual(current['rounds'][0]['snapshot_ids'], ['https://example.com/a'])

    def test_completed_batch_does_not_claim_every_input_was_verified(self):
        cycle = self.cycle()
        self.service._plan_round(cycle)
        cycle = self.service.get(cycle['id'])
        evidence = signed_run()['results']['evidence'] + [{'id': 'e2', 'url': 'https://example.com/b',
                    'text': 'Physical AI', 'origin': 'telegram_excerpt'}]
        self.service._finish_round(cycle, cycle['rounds'][0], signed_run('new', evidence))
        result = self.service.get(cycle['id'])
        self.assertEqual(result['metrics']['total_unique'], 2)
        self.assertEqual(result['metrics']['verified_unique'], 1)
        self.assertEqual(result['metrics']['needs_review_unique'], 1)
        self.assertFalse(result['metrics'].get('all_verified', False))

    def test_url_free_news_with_same_title_require_distinct_text_evidence(self):
        self.items = [{'id': 'a', 'title': '뉴스', 'text': '소버린 AI', 'source_url': ''},
                      {'id': 'b', 'title': '뉴스', 'text': 'Physical AI', 'source_url': ''}]
        cycle = self.cycle()
        self.service._plan_round(cycle)
        cycle = self.service.get(cycle['id'])
        run = signed_run('no-url', [{'id': 'e1', 'url': '', 'origin': 'telegram_excerpt',
            'title': '뉴스', 'text': '뉴스\n\n소버린 AI'}])
        self.service._finish_round(cycle, cycle['rounds'][0], run)
        current = self.service.get(cycle['id'])
        self.assertEqual(current['metrics']['verified_unique'], 1)
        self.assertEqual(current['metrics']['needs_review_unique'], 1)

    def test_changed_processed_news_is_not_still_reported_fully_verified(self):
        self.items = self.items[:1]
        cycle = self.cycle()
        self.service._plan_round(cycle)
        cycle = self.service.get(cycle['id'])
        self.service._finish_round(cycle, cycle['rounds'][0], signed_run('first'))
        self.assertTrue(self.service.get(cycle['id'])['metrics']['all_verified'])
        self.items[0]['text'] = '원문 내용 변경: 수출통제'
        self.service._plan_round(self.service.get(cycle['id']))
        current = self.service.get(cycle['id'])
        self.assertEqual(current['rounds'][-1]['status'], 'planned')
        self.assertFalse(current['metrics']['all_verified'])
        self.assertEqual(current['metrics']['remaining'], 1)

    def test_round_budget_exhaustion_is_not_corpus_completion(self):
        cycle = self.cycle(max_rounds=1)
        self.service._plan_round(cycle)
        cycle = self.service.get(cycle['id'])
        failed = {'id': 'failed', 'status': 'failed', 'error': '분석 실패', 'results': None}
        self.service._finish_round(cycle, cycle['rounds'][0], failed)
        self.service._worker(cycle['id'])
        result = self.service.get(cycle['id'])
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['metrics']['verified_unique'], 0)

    def test_earlier_failed_batch_is_retried_after_later_success(self):
        cycle = self.cycle(max_news=1)
        self.service._plan_round(cycle)
        cycle = self.service.get(cycle['id'])
        self.service._finish_round(cycle, cycle['rounds'][0], {'id': 'failed', 'status': 'failed', 'error': '일시적 실패', 'results': None})
        self.service._plan_round(self.service.get(cycle['id']))
        cycle = self.service.get(cycle['id'])
        self.assertEqual(cycle['rounds'][-1]['snapshot_ids'], ['https://example.com/b'])
        success = signed_run('later', [{'id': 'e1', 'url': 'https://example.com/b',
            'origin': 'telegram_excerpt', 'title': '다른 원문', 'text': 'Physical AI'}])
        self.service._finish_round(cycle, cycle['rounds'][-1], success)
        self.service._plan_round(self.service.get(cycle['id']))
        latest = self.service.get(cycle['id'])['rounds'][-1]
        self.assertEqual(latest['status'], 'planned')
        self.assertEqual(latest['snapshot_ids'], ['https://example.com/a'])
        self.assertTrue(latest['snapshot']['same_snapshot_retry'])


if __name__ == '__main__':
    unittest.main()
