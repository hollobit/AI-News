import copy
import json
import sqlite3
import unittest
from datetime import datetime, timezone

from graph_rag import validated_workflow_content
from risk_analysis import _digest, read_risks, validate_risk_report, validated_risk_content


def fixture(day='2026-09-10'):
    evidence = [{'id': 'e1', 'origin': 'telegram_excerpt', 'url': 'https://example.com/a',
                 'title': '원문 사건', 'text': 'AI 접근 통제 오류 사례가 보고됐다.', 'published_at': day}]
    risk = {'title': '접근 통제 실패', 'current_severity': 'high', 'future_likelihood': 'moderate',
            'horizon': '3-12mo', 'impact_domains': ['security', 'industry'], 'affected_assets': ['모델 접근 시스템'],
            'affected_actors': ['운영자'], 'observed_indicators': ['원문에 오류 사례가 보고됨'],
            'current_basis': '제공된 원문의 사례 범위에 한정한 평가', 'scenario': '접근 통제가 개선되지 않으면 추가 노출 가능',
            'assumptions': ['같은 접근 구조를 유지한다는 조건'], 'escalation_signals': ['유사 오류 재발'],
            'mitigations': ['접근 권한 재검토'], 'counter_evidence': ['독립 확인 자료 없음'],
            'uncertainty': '사례의 범위와 재현성이 불명확함', 'evidence_ids': ['e1']}
    risk_report = {'summary': '현재 사례와 조건부 미래 가능성을 구분했다.', 'risks': [risk],
                   'assessed_evidence_ids': ['e1'], 'not_assessable_evidence_ids': [], 'limitations': ['뉴스 발췌 기준']}
    main_report = {'summary': '전략 검토', 'claims': [{'title': '접근 통제', 'detail': '통제 절차 점검 필요',
        'category': 'risk', 'uncertainty': '원문 범위 한정', 'evidence_ids': ['e1']}], 'limitations': []}
    payload = {'verified': True, 'risk_verified': True, 'report': main_report, 'risk_report': risk_report,
               'evidence': evidence, 'completed_at': '2026-09-15T10:00:00+00:00'}
    for key, report in [('verification', main_report), ('risk_verification', risk_report)]:
        payload[key] = {'accepted': True, 'issues': [], 'checked_evidence_ids': ['e1'], 'limitations': [],
                        'report_hash': _digest(report), 'evidence_hash': _digest(evidence)}
    return payload


class RiskAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT,updated_at TEXT)')
        self.db.execute('CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT)')

    def tearDown(self):
        self.db.close()

    def save(self, run_id, payload, status='complete'):
        self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?)', (run_id, status, '', '2026-09-15'))
        self.db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)', (run_id, 'final', json.dumps(payload)))

    def read(self, params=None):
        return read_risks(self.db, params, current_time=datetime(2026,9,15,tzinfo=timezone.utc))

    def test_present_severity_and_future_likelihood_are_distinct_source_assessments(self):
        payload = fixture()
        self.assertEqual(validate_risk_report(payload['risk_report'], payload['evidence']), payload['risk_report'])
        self.save('r1', payload)
        result = self.read()
        risk = result['risks'][0]
        self.assertEqual(risk['current_severity'], 'high')
        self.assertEqual(risk['future_likelihood'], 'moderate')
        self.assertEqual(risk['horizon'], '3-12mo')
        self.assertEqual(risk['evidence_latest_date'], '2026-09-10')
        self.assertFalse(risk['stale'])
        self.assertIn('2026-09-15', risk['analysis_at'])
        self.assertTrue(risk['evidence'][0]['id'].startswith('workflow:'))
        self.assertNotIn('global_score', result)

    def test_unsupported_ratings_are_unknown_and_numeric_probability_is_rejected(self):
        payload = fixture()
        report = payload['risk_report']
        report['risks'][0].update(current_severity='critical', observed_indicators=[], evidence_ids=[])
        validated = validate_risk_report(report, payload['evidence'])
        self.assertEqual(validated['risks'][0]['current_severity'], 'unknown')
        self.assertEqual(validated['risks'][0]['future_likelihood'], 'unknown')
        payload = fixture()
        payload['risk_report']['risks'][0]['scenario'] = '재발 가능성은 70%다.'
        with self.assertRaises(ValueError):
            validate_risk_report(payload['risk_report'], payload['evidence'])
        payload['risk_report']['risks'][0]['scenario'] = 'Estimated probability is 0.7.'
        with self.assertRaises(ValueError):
            validate_risk_report(payload['risk_report'], payload['evidence'])

    def test_stale_unknown_and_future_source_dates_are_not_current_high_risk(self):
        for index, day in enumerate(['2026-01-01', '', '2027-01-01']):
            payload = fixture(day)
            payload['evidence'][0]['fetched_at'] = '2026-09-15'
            for key in ['verification', 'risk_verification']:
                payload[key]['evidence_hash'] = _digest(payload['evidence'])
            self.save(str(index), payload)
        result = self.read()
        self.assertEqual(result['distributions']['current_severity'], {'unknown': 3})
        self.assertTrue(all(r['assessed_current_severity'] == 'high' for r in result['risks']))
        self.assertEqual({r['date_status'] for r in result['risks']}, {'stale', 'unknown', 'future_dated'})

    def test_independent_audit_hash_and_missing_risk_gate_preserve_legacy(self):
        legacy = fixture()
        for key in ('risk_report', 'risk_verification', 'risk_verified'):
            del legacy[key]
        self.assertTrue(validated_workflow_content(legacy, 'legacy'))
        self.save('legacy', legacy)
        invalid = fixture()
        invalid['risk_report']['risks'][0]['scenario'] = '감사 뒤 수정된 시나리오'
        self.assertFalse(validated_risk_content(invalid))
        self.assertFalse(validated_workflow_content(invalid, 'bad'))
        self.save('bad', invalid)
        self.save('good', fixture())
        result = self.read()
        self.assertEqual(result['coverage']['assessed'], 1)
        self.assertEqual(result['coverage']['needs_review'], 1)
        self.assertEqual(result['coverage']['unassessed'], 1)
        self.assertEqual(len(result['risks']), 1)

    def test_zero_identified_risks_can_still_cover_all_reviewed_sources(self):
        payload = fixture()
        payload['risk_report']['risks'] = []
        payload['risk_verification']['report_hash'] = _digest(payload['risk_report'])
        self.assertTrue(validated_risk_content(payload))
        self.save('zero', payload)
        result = self.read()
        self.assertEqual(result['coverage']['assessed'], 1)
        self.assertEqual(result['coverage']['accepted_risks'], 0)
        self.assertEqual(result['assessments'][0]['assessed_evidence_ids'], ['e1'])
        payload['risk_verification']['checked_evidence_ids'] = []
        self.assertFalse(validated_risk_content(payload))

    def test_unassessable_sources_remain_explicit_and_scope_omissions_fail(self):
        payload = fixture()
        report = payload['risk_report']
        report.update(risks=[], assessed_evidence_ids=[], not_assessable_evidence_ids=['e1'])
        self.assertEqual(validate_risk_report(report, payload['evidence']), report)
        report['not_assessable_evidence_ids'] = []
        with self.assertRaises(ValueError):
            validate_risk_report(report, payload['evidence'])

    def test_fabricated_and_simulated_risk_citations_are_rejected(self):
        payload = fixture()
        payload['risk_report']['risks'][0]['evidence_ids'] = ['fake']
        with self.assertRaises(ValueError):
            validate_risk_report(payload['risk_report'], payload['evidence'])
        payload = fixture()
        payload['evidence'][0]['origin'] = 'mirofish_simulation'
        with self.assertRaises(ValueError):
            validate_risk_report(payload['risk_report'], payload['evidence'])


if __name__ == '__main__':
    unittest.main()

    def test_explanation_fields_cannot_contain_bare_evidence_ids(self):
        for field in ('affected_assets','observed_indicators','limitations'):
            with self.subTest(field=field):
                payload=fixture();report=payload['risk_report']
                target=report if field=='limitations' else report['risks'][0]
                target[field]=['e1']
                payload['risk_verification']['report_hash']=_digest(report)
                with self.assertRaisesRegex(ValueError,'근거 ID만 기록됨'):validate_risk_report(report,payload['evidence'])
                self.assertFalse(validated_risk_content(payload))
                self.save(field,payload)
        result=self.read();self.assertFalse(result['risks'])
        self.assertTrue(all(any('근거 ID만 기록됨' in issue for issue in row['issues']) for row in result['needs_review']))
        payload=fixture();payload['risk_report']['limitations']=['e1 원문 범위의 설명입니다.']
        self.assertTrue(validate_risk_report(payload['risk_report'],payload['evidence']))
