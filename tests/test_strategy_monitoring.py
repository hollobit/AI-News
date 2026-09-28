import sqlite3
import unittest

from morphology import extract_keywords
from strategy_trends import filter_lens, trend_metrics


class StrategyMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')

    def tearDown(self):
        self.db.close()

    def news(self, index, text, url=None, **extra):
        return dict({'message_id': index, 'title': f'기사 {index}', 'text': text,
                     'source_url': url or f'https://example.com/{index}', 'day': '2026-09-10'}, **extra)

    def test_topics_are_permanent_even_when_no_news_matches(self):
        metrics = trend_metrics(self.db, [self.news(1, 'AI 신제품 발표')])
        topics = metrics['monitoring']['topics']
        self.assertEqual(len(topics), 5)
        self.assertTrue(all(t['zero_hits'] and t['count'] == 0 and not t['related_keywords'] for t in topics))
        self.assertTrue({t['id'] for t in topics} <= {t['id'] for t in metrics['lenses']})

    def test_control_types_are_separate_lenses(self):
        cases = {'kill_switch': 'AI kill switch', 'training_pause': 'AI training pause',
                 'training_control': 'AI 안전 정책: 학습 속도를 조절',
                 'compute_limit': 'AI training compute cap', 'deployment_stop': 'AI 배포 중단'}
        for target, text in cases.items():
            with self.subTest(target=target):
                row = self.news(1, text)
                for lens in cases:
                    self.assertEqual(bool(filter_lens([row], lens)), target == lens)

    def test_learning_rate_and_reporting_threshold_are_not_slowdown_or_caps(self):
        rows = [self.news(1, '학습 속도 조절은 learning rate optimizer 최적화 파라미터다.'),
                self.news(2, 'AI training compute threshold triggers reporting obligations.')]
        self.assertEqual(filter_lens(rows, 'training_control'), [])
        self.assertEqual(filter_lens(rows, 'compute_limit'), [])
        terms = {t['label'] for t in extract_keywords('Learning rate 학습률과 training pause')}
        self.assertIn('학습률', terms)
        self.assertIn('학습 일시중단', terms)

    def test_candidates_need_two_actual_unique_cooccurring_documents(self):
        rows = [self.news(1, 'AI kill switch와 소버린 AI', 'https://example.com/a'),
                self.news(2, 'AI kill switch와 소버린 AI', 'https://example.com/a?utm_source=repost'),
                self.news(3, 'AI kill switch와 소버린 AI', 'https://example.com/b'),
                self.news(4, 'AI kill switch와 Physical AI', 'https://example.com/c'),
                self.news(5, 'Physical AI와 관계없는 발표', 'https://example.com/d')]
        metrics = trend_metrics(self.db, rows)
        topic = next(t for t in metrics['monitoring']['topics'] if t['id'] == 'kill_switch')
        self.assertEqual(topic['count'], 3)
        candidate = next(c for c in topic['related_keywords'] if c['label'] == '소버린 AI')
        self.assertEqual(candidate['documents'], 2)
        self.assertEqual(candidate['topic_share_pct'], 66.67)
        self.assertEqual({e['url'] for e in candidate['evidence']}, {'https://example.com/a', 'https://example.com/b'})
        self.assertTrue(all(e['title'] and e['keyword_surface'] for e in candidate['evidence']))
        self.assertNotIn('Physical AI', {c['label'] for c in topic['related_keywords']})
        self.assertNotIn('비상정지', {c['label'] for c in topic['related_keywords']})

    def test_unfetched_source_and_url_path_are_not_monitoring_evidence(self):
        row = self.news(1, 'AI https://example.com/kill-switch', source_context={
            'status': 'failed', 'text': 'AI kill switch와 소버린 AI'})
        self.assertEqual(filter_lens([row], 'kill_switch'), [])
        row['source_context']['status'] = 'fetched'
        self.assertEqual(len(filter_lens([row], 'kill_switch')), 1)

    def test_overlapping_control_aliases_count_one_phrase(self):
        keyword = next(t for t in extract_keywords('pause training runs') if t['label'] == '학습 일시중단')
        self.assertEqual(keyword['count'], 1)
        self.assertEqual(keyword['surface'], 'pause training runs')


if __name__ == '__main__':
    unittest.main()


def test_unrelated_ai_sentence_does_not_turn_car_stop_into_ai_control():
    from strategy_monitoring import match_topic,WATCH_LENSES
    topic=WATCH_LENSES[0]
    assert not match_topic({'text':'AI 모델을 발표했다. 자동차 kill switch를 수리했다.'},topic)
    assert not match_topic({'text':'자동차 모델의 kill switch를 수리했다.'},topic)
    assert match_topic({'text':'AI kill switch 도입을 제안했다.'},topic)


def test_control_matching_preserves_whitespace_boundaries_and_changed_input():
    from strategy_monitoring import control_observations, WATCH_LENSES
    topic=next(t for t in WATCH_LENSES if t['id']=='training_pause')
    row={'text':'AI training\t  pause proposed.'}
    observed=control_observations(row,topic)
    assert observed and 'training pause' in observed[0]['matched_terms']
    assert observed[0]['quote']==row['text']
    row['text']='AI training pausesX proposed.'
    assert not control_observations(row,topic)
    row['text']='AI https://example.org/training-pause'
    assert not control_observations(row,topic)


def test_current_count_quotes_and_conditional_status_are_consistent():
    from strategy_monitoring import build_monitoring
    rows=[{'message_id':1,'title':'이전 기사','text':'AI kill switch 도입을 제안했다.','day':'2026-09-02','source_url':'https://example.org/old'},
          {'message_id':2,'title':'현재 기사','text':'AI kill switch는 도입하지 않았다.','day':'2026-09-10','source_url':'https://example.org/new'}]
    result=build_monitoring(rows,{},[{'id':'kill_switch','current':1,'previous':1}])['topics'][0]
    assert result['count']==1 and result['matched_documents']==2
    assert len(result['evidence'])==1
    assert result['evidence'][0]['quote']==rows[1]['text']
    assert result['observation_types']['negated_or_conditional']==1
