import copy
import json
import sqlite3
import unittest

from baseline_graph import baseline_sources
from bulk_baseline import digest, freeze_item


class BaselineGraphTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE bulk_baseline_cache(input_hash TEXT,result_json TEXT,prepared_json TEXT)')
        self.item = {'title': '소버린 AI 정부 투자', 'text': '정부가 소버린 AI 투자를 발표했다.',
                     'source_url': 'https://example.com/news'}
        self.snapshot = freeze_item(self.item)
        refs = [e['id'] for e in self.snapshot['evidence']]
        report = dict(document_id=self.snapshot['document_id'], summary='소버린 AI 투자 발표',
                      keywords=[dict(label='소버린 AI', source_quote='소버린 AI')],
                      strategic_relevance='정책 관찰', risk_signal='위험 정보 미제시',
                      limitations='발췌 분석', evidence_ids=refs)
        self.result = dict(report, verified=True, input_hash=self.snapshot['input_hash'],
                           evidence=self.snapshot['evidence'], verification=dict(accepted=True, issues=[],
                           checked_evidence_ids=refs, report_hash=digest(report), evidence_hash=digest(self.snapshot['evidence'])))

    def save(self, result=None):
        self.db.execute('INSERT INTO bulk_baseline_cache VALUES (?,?,?)',
                        (self.snapshot['input_hash'], json.dumps(result or self.result),
                         json.dumps({'keywords':[{'label':'소버린 AI','surface':'소버린 AI'}]})))

    def test_verified_summary_and_actual_keyword_have_citation_edges(self):
        self.save()
        sources, counts = baseline_sources(self.db, [self.item])
        self.assertEqual(counts['completed_verified_baselines'], 1)
        graph = sources[0]['result']
        self.assertEqual({e['relation'] for e in graph['edges']}, {'근거 인용', '원문 표현 관측'})
        self.assertTrue(all(e['text'] == self.snapshot['evidence'][0]['text'] for e in graph['evidence']))

    def test_changed_source_excludes_old_analysis(self):
        self.save()
        sources, counts = baseline_sources(self.db, [dict(self.item, text='정부가 투자를 철회했다.')])
        self.assertEqual(sources, [])
        self.assertEqual(counts['baseline_stale_or_invalid'], 1)

    def test_mutated_report_or_unchecked_evidence_is_rejected(self):
        for mutation in ('summary', 'checked'):
            self.db.execute('DELETE FROM bulk_baseline_cache')
            result = copy.deepcopy(self.result)
            if mutation == 'summary': result['summary'] = '근거 없는 변경'
            else: result['verification']['checked_evidence_ids'] = []
            self.save(result)
            self.assertEqual(baseline_sources(self.db, [self.item])[0], [])

    def test_analysis_text_cannot_replace_original_evidence(self):
        result = copy.deepcopy(self.result)
        result['evidence'][0]['text'] = '모델이 새로 작성한 내용'
        self.save(result)
        self.assertEqual(baseline_sources(self.db, [self.item])[0], [])
