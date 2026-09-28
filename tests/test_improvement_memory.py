import copy
import hashlib
import json
import sqlite3
import unittest

from improvement_memory import improve_catalog, list_catalog, catalog_history


def workflow(run_id='w1', status='complete'):
    payload = {'verified': True, 'report': {'summary': '공동관리 검토', 'claims': [{
        'title': '컴퓨트 안전 공동관리', 'detail': '공동 관리라는 전략 개념을 제안한다.',
        'category': 'strategic_concept', 'evidence_ids': ['e1'], 'uncertainty': '독립 원문 추가 확인 필요'}], 'limitations': []},
        'evidence': [{'id': 'e1', 'text': '소버린 AI와 수출통제', 'url': 'https://example.com/a',
                      'title': '원문 발표', 'origin': 'telegram_excerpt'}],
        'verification': {'accepted': True, 'issues': [], 'checked_evidence_ids': ['e1']}, 'coverage': {}}
    sign(payload)
    return {'id': run_id, 'status': status, 'results': payload, 'error': ''}


def sign(payload):
    for key, value in [('report_hash', payload['report']), ('evidence_hash', payload['evidence'])]:
        payload['verification'][key] = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ImprovementMemoryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')

    def tearDown(self):
        self.db.close()

    def test_observed_terms_proposals_and_citation_relations_stay_distinct(self):
        result = improve_catalog(self.db, 'w1', workflow())
        self.assertTrue(result['accepted'])
        entries = list_catalog(self.db)['items']
        self.assertEqual({e['kind'] for e in entries}, {'observed_keyword', 'strategic_concept', 'claim_relation'})
        observed = [e for e in entries if e['kind'] == 'observed_keyword']
        self.assertEqual({e['label'] for e in observed}, {'소버린 AI', '수출통제'})
        concept = next(e for e in entries if e['kind'] == 'strategic_concept')
        self.assertEqual(concept['epistemic_status'], 'proposed')
        relation = next(e for e in entries if e['kind'] == 'claim_relation')
        self.assertEqual(relation['relation'], '근거 인용')
        self.assertIn('인과관계', relation['caveat'])
        task = result['followup_tasks'][0]
        self.assertEqual(set(task['search_terms']), {'소버린 AI', '수출통제'})
        self.assertNotIn(concept['label'], task['search_terms'])
        self.assertTrue(all(ref.startswith('workflow:') for e in entries for ref in e['evidence_ids']))

    def test_duplicate_ingestion_is_idempotent_new_provenance_has_history(self):
        original = improve_catalog(self.db, 'w1', workflow())
        again = improve_catalog(self.db, 'w1', workflow())
        self.assertEqual(again['added'], [])
        self.assertEqual(again['updated'], [])
        self.assertEqual(again['catalog_version'], original['catalog_version'])
        next_run = improve_catalog(self.db, 'w2', workflow('w2'))
        self.assertEqual(next_run['added'], [])
        self.assertEqual(len(next_run['updated']), len(original['added']))
        for entry in next_run['updated']:
            self.assertEqual(entry['version'], 2)
            self.assertEqual(entry['run_ids'], ['w1', 'w2'])
            self.assertEqual(entry['support_count'], 1)
            self.assertEqual(entry['evidence'][0]['workflow_run_ids'], ['w1', 'w2'])
            history = catalog_history(self.db, entry['id'])
            self.assertEqual([e['version'] for e in history], [1, 2])
            self.assertEqual(history[0]['run_ids'], ['w1'])

    def test_rejected_or_stale_or_simulated_results_never_expand_catalog(self):
        for index, mutate in enumerate([
            lambda w: w.update(status='needs_review'),
            lambda w: w.update(status='failed'),
            lambda w: w['results'].update(verified=False),
            lambda w: w['results']['verification'].update(accepted=False),
            lambda w: w['results']['verification'].update(checked_evidence_ids=[]),
            lambda w: w['results']['report'].update(summary='감사 후 보고서 변경'),
            lambda w: w['results']['evidence'][0].update(text='감사 후 원문 변경'),
        ]):
            item = workflow(f'bad{index}')
            mutate(item)
            result = improve_catalog(self.db, item['id'], item)
            self.assertFalse(result['accepted'])
            self.assertTrue(result['followup_tasks'])
        item = workflow('simulated')
        item['results']['evidence'][0]['origin'] = 'mirofish_simulation'
        sign(item['results'])
        self.assertFalse(improve_catalog(self.db, item['id'], item)['accepted'])
        self.assertEqual(list_catalog(self.db)['items'], [])

    def test_reviewer_feedback_proposes_checks_without_changing_scores(self):
        item = workflow(status='needs_review')
        item['results']['verification'].update(accepted=False, issues=['국가 영향 근거 부족'])
        result = improve_catalog(self.db, 'w1', item)
        self.assertEqual(result['rule_proposals'][0]['status'], 'proposed')
        self.assertIn('변경하지 않음', result['rule_proposals'][0]['effect'])
        self.assertTrue(result['followup_tasks'][0]['search_terms'])
        self.assertEqual(list_catalog(self.db)['version'], 0)

    def test_changed_concept_meaning_retains_previous_version(self):
        improve_catalog(self.db, 'w1', workflow())
        revised = workflow('w2')
        revised['results']['report']['claims'][0]['detail'] = '비용 부담을 포함하여 공동 관리를 검토한다.'
        sign(revised['results'])
        improve_catalog(self.db, 'w2', revised)
        concept = next(e for e in list_catalog(self.db)['items'] if e['kind'] == 'strategic_concept')
        history = catalog_history(self.db, concept['id'])
        self.assertEqual(len(history), 2)
        self.assertNotEqual(history[0]['detail'], history[1]['detail'])
        self.assertEqual(concept['epistemic_status'], 'proposed')


if __name__ == '__main__':
    unittest.main()
