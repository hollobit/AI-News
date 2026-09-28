from copy import deepcopy

from completion_quality import document_admission
from strategic_workflow import digest


def fixture():
    item = {'title': 'AI 배포 중단 제안', 'source_url': 'https://example.com/policy',
            'source_context': {'status': 'fetched', 'text': '운영자는 평가 후 재개 여부를 결정한다고 밝혔다.'}}
    evidence = [{'id': 'news_a', 'origin': 'telegram_excerpt', 'url': item['source_url'],
                 'text': item['title'], 'title': item['title']},
                {'id': 'url_a', 'origin': 'fetched_url_excerpt', 'url': item['source_url'],
                 'text': item['source_context']['text'], 'title': item['title']}]
    report = {'summary': '운영 중단 제안이 보도됐다.', 'claims': [{'title': '배포 중단 제안',
              'detail': '관측: 운영 중단 제안이 언급됐다.', 'category': 'watch_signal',
              'uncertainty': '실제 중단 여부는 확인되지 않는다.', 'evidence_ids': ['news_a', 'url_a']}],
              'limitations': ['제공 발췌 범위']}
    risk = {'summary': '현 피해를 판단할 근거가 부족하다.', 'risks': [],
            'assessed_evidence_ids': [], 'not_assessable_evidence_ids': ['news_a', 'url_a'],
            'limitations': ['피해 지표가 제공되지 않았다.']}
    def audit(value):
        return {'accepted': True, 'issues': [], 'limitations': [], 'checked_evidence_ids': ['news_a', 'url_a'],
                'report_hash': digest(value), 'evidence_hash': digest(evidence)}
    result = {'report': report, 'verification': audit(report), 'risk_report': risk,
              'risk_verification': audit(risk), 'evidence': evidence, 'verified': True, 'risk_verified': True}
    return {'id': 'sample', 'status': 'complete', 'error': '', 'results': result}, item


def test_explicit_insufficient_risk_evidence_is_reviewed_without_becoming_assessed():
    run, item = fixture()
    result = document_admission(run, item)
    assert result['complete'] and result['risk_reviewed']
    assert not result['risk_assessed']


def test_retracted_source_cannot_reuse_old_successful_analysis():
    run, item = fixture()
    item['source_context'] = {'status': 'failed', 'text': ''}
    result = document_admission(run, item)
    assert not result['current_snapshot'] and not result['complete']


def test_same_url_different_story_cannot_reuse_audit():
    run, item = fixture()
    item['title'] = '다른 기사 제목'
    assert not document_admission(run, item)['complete']


def test_unchecked_not_assessable_is_not_a_complete_risk_review():
    run, item = fixture()
    run['results']['risk_verification']['checked_evidence_ids'] = []
    assert not document_admission(run, item)['risk_reviewed']


def test_hash_mismatch_never_admitted():
    run, item = fixture()
    run['results']['report']['claims'][0]['detail'] = '허가 없이 바뀐 해석'
    assert not document_admission(run, item)['complete']


def test_unknown_risk_horizon_does_not_force_a_fabricated_forecast():
    from risk_analysis import validate_risk_report, validated_risk_content
    from test_risk_analysis import fixture as risk_fixture
    result = risk_fixture('2026-09-10')
    report = result['risk_report']
    report['risks'][0]['horizon'] = 'unknown'
    report['risks'][0]['uncertainty'] = '발생 시점을 뒷받침하는 관측 자료가 없다.'
    result['risk_verification']['report_hash'] = digest(report)
    assert validate_risk_report(report, result['evidence']) == report
    assert validated_risk_content(result)['risks'][0]['horizon'] == 'unknown'
