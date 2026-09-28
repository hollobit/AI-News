import unittest

from strategic_value import evaluate_news


class StrategicValueTests(unittest.TestCase):
    def test_explicit_public_impact_outweighs_generic_company_news(self):
        ordinary = evaluate_news({'text': '중국 정부 OpenAI 신규 모델 발표'})
        impactful = evaluate_news({'text': '중국 정부 수출통제가 국가경제와 국방에 영향을 준다.'})
        self.assertEqual(ordinary['domains'], [])
        self.assertNotEqual(ordinary['level'], 'high')
        self.assertGreater(impactful['score'], ordinary['score'])
        self.assertEqual(impactful['score'], 100)
        self.assertEqual(impactful['impact_factor'], .6)
        self.assertTrue({'economy', 'security', 'exports'} <= {d['id'] for d in impactful['domains']})

    def test_all_requested_impact_domains_have_traceable_terms(self):
        cases = {'economy': '고용과 생산성', 'security': '국방 투자', 'industry': '산업생산',
                 'exports': '수출 경쟁력', 'social': '디지털 격차', 'life': '돌봄 서비스', 'education': '학교 교육'}
        for domain, text in cases.items():
            with self.subTest(domain=domain):
                value = evaluate_news({'text': text})
                entry = next(d for d in value['domains'] if d['id'] == domain)
                self.assertTrue(entry['matched_terms'])
                self.assertGreater(entry['weight'], 0)
                self.assertGreater(value['score'], 0)

    def test_failed_sources_urls_and_generated_metadata_do_not_add_priority(self):
        for status in ('failed', 'blocked', 'pending'):
            value = evaluate_news({'text': 'OpenAI https://example.com/national-security/exports',
                                   'source_context': {'status': status, 'title': '국가안보', 'text': '수출통제 교육'},
                                   'strategic_value': {'score': 100}, 'analysis': '국가경제'})
            self.assertEqual(value['score'], 0)
        value = evaluate_news({'text': 'OpenAI', 'source_context': {'status': 'fetched', 'text': '교육'}})
        self.assertEqual(value['domains'][0]['id'], 'education')

    def test_english_word_boundaries_and_repetition_do_not_inflate_scores(self):
        self.assertEqual(evaluate_news({'text': 'exporter semiconductorish schooling'})['score'], 0)
        text = 'United States government export controls, economic growth and national security'
        once, repeated = evaluate_news({'text': text}), evaluate_news({'text': (text + ' ') * 20})
        self.assertEqual(once, repeated)
        self.assertEqual(once['impact_score'], 60)
        self.assertIn('exports', {d['id'] for d in once['domains']})

    def test_education_is_not_generic_machine_learning(self):
        value = evaluate_news({'text': '강화학습 알고리즘 reinforcement learning algorithm'})
        self.assertEqual(value['domains'], [])


if __name__ == '__main__':
    unittest.main()
