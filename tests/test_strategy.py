"""Regression checks for source provenance and strategy trend denominators."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from source_enrichment import SourceService
from strategy_trends import filter_lens, filter_strategic_keyword, trend_metrics
from semantic import input_hash


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'news.sqlite3'
        self.db = app.connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_primary_source_enriches_keywords_without_changing_original(self):
        app.save_message(self.db, {'message_id': 1, 'chat': {'id': -1001, 'type': 'channel', 'title': 'test'},
                                  'date': 1789167600, 'text': 'AI 모델\nhttps://example.com/a'})
        self.db.commit()
        source = SourceService(self.path, fetcher=lambda url: {'status': 'fetched', 'text': 'Sovereign computing robotics', 'title': 'Source'})
        try:
            source.fetch('https://example.com/a')
            self.db.close()
            self.db = app.connect(self.path)
            rows = app.read_news(self.db, {'date': ['all']})['items']
            self.assertEqual(rows[0]['source_context']['status'], 'fetched')
            self.assertNotIn('Sovereign', self.db.execute('SELECT text FROM news').fetchone()[0])
            self.assertTrue(self.db.execute("SELECT 1 FROM keyword_terms WHERE normalized='sovereign'").fetchone())
            self.assertTrue(filter_lens(rows, 'sovereign'))
        finally:
            source.close()

    def test_failed_source_is_not_keyword_or_lens_evidence(self):
        item = {'title':'generic','text':'generic','source_context': {'status':'failed','text':'sovereign'}}
        self.assertEqual(filter_lens([item], 'sovereign'), [])
        self.assertEqual(filter_lens([{'text':'chart apart vlarge'}], 'physical'), [])

    def test_growth_deduplicates_urls_and_normalizes_share(self):
        rows = [
            {'chat_id':'1','message_id':1,'item_index':0,'day':'2026-09-01','source_url':'https://x.test/a','text':'中国 sovereign'},
            {'chat_id':'1','message_id':2,'item_index':0,'day':'2026-09-08','source_url':'https://x.test/a','text':'sovereign'},
            {'chat_id':'1','message_id':3,'item_index':0,'day':'2026-09-09','source_url':'https://x.test/a','text':'sovereign'},
            {'chat_id':'1','message_id':4,'item_index':0,'day':'2026-09-10','source_url':'https://x.test/b','text':'other'},
        ]
        t=trend_metrics(self.db, rows)
        sovereign=next(l for l in t['lenses'] if l['id']=='sovereign')
        self.assertEqual(sovereign['previous'],1)
        self.assertEqual(sovereign['current'],1)
        self.assertEqual(sovereign['growth_pct'],0)
        self.assertEqual(sovereign['share_change_pp'],-50)

    def test_source_changes_invalidate_link_analysis(self):
        g={'mentions':[{'id':'a','text':'original'}]}
        before=input_hash(g)
        g['source_context']={'status':'fetched','text':'new context'}
        self.assertNotEqual(before,input_hash(g))

    def test_failed_fetch_is_cached_and_not_silently_promoted(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            return {'status':'blocked','text':'should not become evidence'}
        service=SourceService(self.path, fetcher=fetch)
        try:
            result=service.fetch('https://example.com/x')
            self.assertEqual(result['text'],'')
            self.assertEqual(service.fetch('https://example.com/x')['status'],'blocked')
            self.assertEqual(len(calls),1)
        finally: service.close()

    def test_simulation_scope_respects_lens_and_limit(self):
        rows=[{'text':'robot','source_url':'https://example.com/a'},
              {'text':'robot','source_url':'https://example.com/b'}, {'text':'other'}]
        with patch('app.read_news', return_value={'items':rows}):
            selected=app.simulation_news(self.db, {'filters':{'lens':'physical'},'limit':1})
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['source_url'],'https://example.com/a')

    def test_government_weight_changes_rank_not_counts_or_growth(self):
        rows=[{'message_id':0, 'day':'2026-09-01', 'text':'일반 뉴스', 'source_url':'https://example.com/baseline'}]
        for index in range(3):
            for kind, text in [('policy','미국 정부가 Physical AI를 지원한다.'),
                               ('ordinary','소버린 AI를 발표했다.')]:
                rows.append({'message_id':f'{kind}-{index}', 'day':f'2026-09-{8+index:02}',
                             'text':text,'source_url':f'https://example.com/{kind}/{index}'})
        terms=trend_metrics(self.db, rows)['emerging']
        policy=next(t for t in terms if t['label']=='Physical AI')
        ordinary=next(t for t in terms if t['label']=='소버린 AI')
        for field in ('current','previous','growth_pct','share','share_change_pp'):
            self.assertEqual(policy[field],ordinary[field])
        self.assertEqual(policy['strategic_multiplier'],1.7)
        self.assertEqual(ordinary['strategic_multiplier'],1)
        self.assertGreater(policy['strategic_score'],ordinary['strategic_score'])
        self.assertLess(terms.index(policy),terms.index(ordinary))

    def test_canonical_alias_filter_and_trend_counts(self):
        from morphology import extract_keywords
        term=next(t for t in extract_keywords('강화 학습') if t['label']=='강화학습')
        rows=[{'message_id':index,'day':f'2026-09-{8+index:02}',
               'source_url':f'https://example.com/{index}', 'text':text}
              for index,text in enumerate(['강화 학습','reinforcement learning','강화학습','학습'])]
        selected=filter_strategic_keyword(self.db,rows,{'strategic_keyword':[term['id']]})
        self.assertEqual(len(selected),3)
        candidate=next(t for t in trend_metrics(self.db,rows)['emerging'] if t['id']==term['id'])
        self.assertEqual(candidate['current'],3)

    def test_public_impact_keyword_ranks_higher_without_changing_raw_growth(self):
        rows=[{'message_id':0,'day':'2026-09-01','source_url':'https://example.com/baseline','text':'일반 뉴스'}]
        for index in range(3):
            for kind,text in [('impact','미국 정부 Physical AI 수출통제와 국가경제'),
                              ('normal','미국 정부 소버린 AI 발표')]:
                rows.append({'message_id':f'{kind}-{index}', 'day':f'2026-09-{8+index:02}',
                             'text':text, 'source_url':f'https://example.com/{kind}/{index}'})
        terms=trend_metrics(self.db,rows)['emerging']
        impact=next(t for t in terms if t['label']=='Physical AI')
        normal=next(t for t in terms if t['label']=='소버린 AI')
        self.assertEqual(impact['strategic_multiplier'],2.3)
        self.assertEqual(normal['strategic_multiplier'],1.7)
        self.assertEqual(impact['high_impact_documents'],3)
        self.assertEqual(impact['impact_mean_score'],60)
        self.assertTrue(all(d['documents']==3 for d in impact['impact_domains']))
        for field in ('current','previous','growth_pct','share','share_change_pp'):
            self.assertEqual(impact[field],normal[field])
        self.assertGreater(impact['strategic_score'],normal['strategic_score'])
        # Repeated coverage of the same original URL cannot inflate weighting.
        rows.append(dict(rows[1],message_id='repost'))
        repeated=next(t for t in trend_metrics(self.db,rows)['emerging'] if t['label']=='Physical AI')
        for field in ('current','strategic_score','strategic_multiplier','high_impact_documents','impact_domains'):
            self.assertEqual(repeated[field],impact[field])

    def test_morphology_extraction_does_not_hold_writer_lock(self):
        import sqlite3
        from morphology import keyword_records, extract_keywords
        self.db.execute('CREATE TABLE independent_writer (value TEXT)')
        self.db.commit()
        def extracting(text):
            other=sqlite3.connect(self.path, timeout=0.1)
            try:
                other.execute("INSERT INTO independent_writer VALUES ('collected')")
                other.commit()
            finally:
                other.close()
            return extract_keywords(text)
        # Isolate this cold-cache lock test from the bounded process-wide cache.
        with patch('morphology.ENGINE_VERSION', 'writer-lock-test'), patch('morphology.extract_keywords', side_effect=extracting) as extract:
            rows=[{'message_id':1,'text':'강화 학습'}, {'message_id':2,'text':'Physical AI'}]
            first=keyword_records(self.db,rows)
            self.assertEqual(extract.call_count,2)
            self.assertEqual(keyword_records(self.db,rows),first)
            self.assertEqual(extract.call_count,2)


if __name__=='__main__': unittest.main()

def test_medical_and_public_ax_are_separate_evidence_backed_lenses():
    from strategy_trends import filter_lens
    rows=[{'title':'병원 임상 의료 AI 도입','text':'환자 진료 지원'},
          {'title':'공공 AX 정부 AI 서비스','text':'행정 업무 전환'},
          {'title':'게임 그래픽 업데이트','text':'신규 장면'}]
    assert filter_lens(rows,'medical')==[dict(rows[0],matched_terms=['의료','임상','환자'])]
    public=filter_lens(rows,'public_ax')
    assert len(public)==1 and public[0]['title']==rows[1]['title']
