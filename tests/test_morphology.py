import unittest
from morphology import extract_keywords

class MorphologyTests(unittest.TestCase):
    def labels(self,text):
        return {term['label'] for term in extract_keywords(text)}

    def test_particles_and_endings_are_not_keywords(self):
        terms=self.labels('로봇의 행동으로 학습하고 있다. 정부는 기술을 발표했다.')
        self.assertFalse(terms & {'로봇의','행동으로','정부는','기술을','발표했다','하고','있다'})
        self.assertIn('로봇',terms)

    def test_technical_phrases_and_aliases_survive(self):
        terms=self.labels('소버린 AI와 Physical AI에서 강화 학습과 수출 통제가 중요하다.')
        self.assertTrue({'소버린 AI','Physical AI','강화학습','수출통제'} <= terms)
        self.assertFalse({'소','버린','피','지컬','수출','통제'} & terms)

    def test_original_spans_and_pos_are_retained(self):
        text='한국에서 강화 학습과 데이터 주권을 논의한다.'
        for item in extract_keywords(text):
            self.assertEqual(item['surface'],text[item['start']:item['end']])
            self.assertTrue(item['pos'])

    def test_url_fragments_do_not_generate_terms(self):
        terms=self.labels('로봇 뉴스 https://example.com/category/ArtificialNonsense?status=1')
        self.assertFalse(any('Nonsense' in term or 'category' in term for term in terms))

    def test_model_identifiers_are_atomic(self):
        terms=self.labels('DeepSeek-V3.2와 GPT-5.4를 비교한다.')
        self.assertIn('DeepSeek-V3.2',terms)
        self.assertIn('GPT-5.4',terms)
        self.assertFalse({'GPT', 'GPT-5', 'DeepSeek', 'DeepSeek-V3'} & terms)
        self.assertIn('GPT-5.4', self.labels('New GPT-5.4.'))

    def test_unicode_prefix_preserves_original_offsets(self):
        text='① ㍿ Straße İ 강화 학습과 GPT-5.4를 소개한다.'
        for item in extract_keywords(text):
            self.assertEqual(item['surface'], text[item['start']:item['end']])

    def test_aliases_have_one_stable_identity(self):
        first=next(t for t in extract_keywords('강화 학습') if t['label']=='강화학습')
        second=next(t for t in extract_keywords('reinforcement learning') if t['label']=='강화학습')
        self.assertEqual(first['id'], second['id'])
        combined=next(t for t in extract_keywords('강화학습 reinforcement learning') if t['label']=='강화학습')
        self.assertEqual(combined['count'],2)

    def test_generic_countries_and_english_verbs_are_not_candidates(self):
        terms=self.labels('미국 정부 중국 규제 핵심 정책. Building growing announcing improved provides.')
        self.assertFalse(terms)
