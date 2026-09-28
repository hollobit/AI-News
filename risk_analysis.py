"""Separate present-risk assessments from conditional future scenarios."""
from collections import Counter
from datetime import date, datetime, timezone
import copy
import hashlib
import json
import re


SEVERITIES = ['unknown', 'low', 'moderate', 'high', 'critical']
LIKELIHOODS = ['unknown', 'low', 'moderate', 'high']
HORIZONS = ['unknown', '0-3mo', '3-12mo', '12-36mo']
DOMAINS = ['economy', 'security', 'industry', 'exports', 'social', 'life', 'education']
TEXT_FIELDS = ['title', 'current_basis', 'scenario', 'uncertainty']
LIST_FIELDS = ['affected_assets', 'affected_actors', 'observed_indicators', 'assumptions',
               'escalation_signals', 'mitigations', 'counter_evidence', 'evidence_ids']
_STRING = {'type': 'string'}
_STRINGS = {'type': 'array', 'items': _STRING}
_PROPERTIES = {key: _STRING for key in TEXT_FIELDS} | {key: _STRINGS for key in LIST_FIELDS}
_PROPERTIES.update(current_severity={'type': 'string', 'enum': SEVERITIES},
                   future_likelihood={'type': 'string', 'enum': LIKELIHOODS},
                   horizon={'type': 'string', 'enum': HORIZONS},
                   impact_domains={'type': 'array', 'items': {'type': 'string', 'enum': DOMAINS}})
RISK_SCHEMA = {'type': 'object', 'properties': {'summary': _STRING, 'risks': {'type': 'array',
    'items': {'type': 'object', 'properties': _PROPERTIES, 'required': list(_PROPERTIES), 'additionalProperties': False}},
    'assessed_evidence_ids': _STRINGS, 'not_assessable_evidence_ids': _STRINGS, 'limitations': _STRINGS},
    'required': ['summary', 'risks', 'assessed_evidence_ids', 'not_assessable_evidence_ids', 'limitations'], 'additionalProperties': False}
RISK_AUDIT_SCHEMA = {'type': 'object', 'properties': {'accepted': {'type': 'boolean'}, 'issues': _STRINGS,
    'checked_evidence_ids': _STRINGS, 'limitations': _STRINGS},
    'required': ['accepted', 'issues', 'checked_evidence_ids', 'limitations'], 'additionalProperties': False}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _sources(evidence):
    return {item['id']: item for item in evidence if isinstance(item, dict) and isinstance(item.get('id'), str)
            and item.get('origin') in {'telegram_excerpt', 'fetched_url_excerpt', 'external_source', 'external_watch'}
            and isinstance(item.get('text'), str) and item['text'].strip()
            and item.get('status', 'fetched') not in {'failed', 'blocked', 'needs_review'}}


def validate_risk_report(report, evidence):
    """Validate bounded records and reduce unsupported ratings to unknown.

    Linguistic support is independently reviewed by the workflow's risk auditor;
    presence of citations is only a structural check here. Empty risks are valid.
    """
    if (not isinstance(report, dict) or not isinstance(report.get('summary'), str)
            or len(report['summary']) > 1400 or not isinstance(report.get('risks'), list) or len(report['risks']) > 6):
        raise ValueError('위험 보고서 형식 또는 최대 6개 제한 오류')
    result = copy.deepcopy(report)
    valid_ids = set(_sources(evidence))
    for key in ('assessed_evidence_ids', 'not_assessable_evidence_ids', 'limitations'):
        values = result.get(key)
        if (not isinstance(values, list) or len(values) > 48
                or any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in values)):
            raise ValueError('위험 평가 범위·한계 형식 오류')
    if any(value.strip() in valid_ids for value in result['limitations']):
        raise ValueError('위험 설명란에 근거 ID만 기록됨: limitations; 실제 설명 작성과 독립 재검토가 필요합니다.')
    assessed, unavailable = set(result['assessed_evidence_ids']), set(result['not_assessable_evidence_ids'])
    if assessed & unavailable or assessed | unavailable != valid_ids:
        raise ValueError('모든 원문을 위험 검토 또는 평가 불가로 구분해야 합니다.')
    if unavailable and not result['limitations']:
        raise ValueError('평가 불가 원문의 한계를 설명해야 합니다.')
    for risk in result['risks']:
        if not isinstance(risk, dict) or any(not isinstance(risk.get(key), str) or len(risk[key]) > 900 for key in TEXT_FIELDS):
            raise ValueError('위험 분석 필수 문장 형식 오류')
        if not risk['title'].strip() or not risk['uncertainty'].strip():
            raise ValueError('위험 제목과 불확실성 설명이 필요합니다.')
        for key in LIST_FIELDS + ['impact_domains']:
            values = risk.get(key)
            if (not isinstance(values, list) or len(values) > (48 if key == 'evidence_ids' else 8)
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in values)):
                raise ValueError('위험 목록 형식 오류: ' + key)
        for key in LIST_FIELDS:
            if key!='evidence_ids' and any(value.strip() in valid_ids for value in risk[key]):
                raise ValueError('위험 설명란에 근거 ID만 기록됨: '+key+'; 실제 설명 작성과 독립 재검토가 필요합니다.')
        if (risk.get('current_severity') not in SEVERITIES or risk.get('future_likelihood') not in LIKELIHOODS
                or risk.get('horizon') not in HORIZONS or not set(risk['impact_domains']) <= set(DOMAINS)):
            raise ValueError('위험 등급·기간·영향 분야 오류')
        if not set(risk['evidence_ids']) <= assessed:
            raise ValueError('위험 분석에 존재하지 않거나 허용되지 않은 원문 인용')
        future = ' '.join([risk['scenario'], *risk['assumptions']])
        if re.search(r'(?:확률|가능성|probability|chance)\s*(?:은|는|이|가|of|is|:|=)?\s*\d+(?:\.\d+)?|(?:확률|가능성|probability|chance)[^.!?\n]{0,24}\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*%[^.!?\n]{0,24}(?:확률|가능성|probability|chance)', future, re.I):
            raise ValueError('미래 가능성에 숫자 확률을 부여하지 마세요.')
        if not risk['evidence_ids'] or not risk['observed_indicators'] or not risk['current_basis'].strip():
            risk['current_severity'] = 'unknown'
        if not risk['evidence_ids'] or not risk['scenario'].strip() or not risk['assumptions']:
            risk['future_likelihood'] = 'unknown'
    return result


def validated_risk_content(payload, run_id=''):
    """Validate only the independent risk gate; callers also check overall status."""
    if not isinstance(payload, dict) or payload.get('risk_verified') is not True:
        return {}
    report, audit, evidence = payload.get('risk_report'), payload.get('risk_verification'), payload.get('evidence')
    if (not isinstance(audit, dict) or audit.get('accepted') is not True or audit.get('issues') != []
            or not isinstance(evidence, list) or audit.get('report_hash') != _digest(report)
            or audit.get('evidence_hash') != _digest(evidence)):
        return {}
    try:
        if validate_risk_report(report, evidence) != report:
            return {}
    except (ValueError, TypeError, KeyError):
        return {}
    checked = audit.get('checked_evidence_ids')
    ids = set(_sources(evidence))
    refs = {ref for risk in report['risks'] for ref in risk['evidence_ids']}
    if (not isinstance(checked, list) or not all(isinstance(ref, str) and ref in ids for ref in checked)
            or not (refs | set(report['assessed_evidence_ids'])).issubset(checked)):
        return {}
    return {'report': report, 'risks': report['risks'], 'evidence': evidence, 'workflow_run_id': run_id}


def read_risks(db, params=None, *, current_time=None, unlimited=False, include_source_evidence=False):
    """Summarize saved source assessments; never calculate a world threat score."""
    from graph_rag import _table_rows, _json, _global_evidence, validated_workflow_content
    params = params or {}
    def one(key):
        value = params.get(key, '')
        return str(value[0] if isinstance(value, list) and value else value or '')
    domain, since = one('domain') or one('impact'), one('since')
    if domain and domain not in DOMAINS:
        raise ValueError('알 수 없는 영향 분야입니다.')
    if since:
        date.fromisoformat(since)
    today = (current_time or datetime.now(timezone.utc)).date()
    rows = _table_rows(db, 'strategic_workflow_runs')
    finals = {row['run_id']: _json(row.get('payload_json')) for row in _table_rows(db, 'strategic_workflow_artifacts') if row.get('stage') == 'final'}
    risks, assessments, pending = [], [], []
    unassessed = 0
    scoped_workflows = 0
    for row in rows:
        payload = finals.get(row['id'])
        payload = payload if isinstance(payload, dict) else {}
        analyzed_at = payload.get('completed_at') or row.get('updated_at') or ''
        if since and str(analyzed_at)[:10] < since:
            continue
        scoped_workflows += 1
        if not isinstance(payload.get('risk_report'), dict):
            unassessed += 1
            continue
        content = validated_risk_content(payload, row['id'])
        if (row.get('status') != 'complete' or row.get('error') or not content
                or not validated_workflow_content(payload, row['id'])):
            issues = (payload.get('risk_verification') or {}).get('issues') or ['위험 분석 승인·원문 해시 검증 미완료']
            try:validate_risk_report(payload.get('risk_report'),payload.get('evidence') or [])
            except (ValueError,TypeError,KeyError) as exc:
                if '근거 ID만 기록됨' in str(exc):issues=list(issues)+[str(exc)]
            pending.append({'workflow_run_id': row['id'], 'status': 'needs_review', 'issues': issues})
            continue
        selected = [risk for risk in content['risks'] if not domain or domain in risk['impact_domains']]
        assessments.append({'workflow_run_id': row['id'], 'analysis_at': analyzed_at,
                            'risk_count': len(selected), 'summary': content['report']['summary'],
                            'assessed_evidence_ids': content['report']['assessed_evidence_ids'],
                            'not_assessable_evidence_ids': content['report']['not_assessable_evidence_ids'],
                            'limitations': content['report']['limitations']})
        raw = _sources(content['evidence'])
        for risk in selected:
            evidence = [_global_evidence(dict(raw[ref], source_url=raw[ref].get('url', ''), workflow_run_ids=[row['id']]),
                                         'workflow', ref, 'workflow') for ref in dict.fromkeys(risk['evidence_ids'])]
            days = []
            for item in evidence:
                for value in item['days']:
                    try:
                        days.append(date.fromisoformat(value))
                    except ValueError:
                        pass
            latest = max(days) if days else None
            age = (today-latest).days if latest else None
            extra={'raw_evidence':[raw[ref] for ref in dict.fromkeys(risk['evidence_ids'])],
                   'assessment_report_hash':(payload.get('risk_verification') or {}).get('report_hash')} if include_source_evidence else {}
            risks.append(dict(risk, **extra, id='risk:'+_digest([row['id'], risk])[:24], workflow_run_id=row['id'],
                analysis_at=analyzed_at, evidence_latest_date=latest.isoformat() if latest else '', source_age_days=age,
                stale=age > 30 if age is not None else None, evidence=evidence,
                assessed_current_severity=risk['current_severity'],
                current_severity=risk['current_severity'] if age is not None and 0 <= age <= 30 else 'unknown',
                date_status='unknown' if age is None else 'future_dated' if age < 0 else 'stale' if age > 30 else 'recent',
                assessment_kind='source_based_assessment' if evidence and risk['observed_indicators'] else 'hypothesis',
                current_basis_scope='분석 시점의 원문 기반 평가; 현재 세계 상황에 대한 확정이 아님'))
    risks.sort(key=lambda item: (str(item['analysis_at']), item['id']), reverse=True)
    return {'risks': risks if unlimited else risks[:200], 'assessments': assessments, 'needs_review': pending,
            'coverage': {'total_workflows': scoped_workflows, 'archive_workflows': len(rows), 'assessed': len(assessments), 'needs_review': len(pending),
                         'unassessed': unassessed, 'accepted_risks': len(risks)},
            'distributions': {'current_severity': dict(Counter(r['current_severity'] for r in risks)),
                              'future_likelihood': dict(Counter(r['future_likelihood'] for r in risks))},
            'truncated': not unlimited and len(risks) > 200,
            'limitations': ['저장된 원문 기반 위험 평가의 개수·등급 분포이며 세계 전체 위험 점수가 아닙니다.',
                            '미래 가능성은 조건부 시나리오이고 숫자 확률이 아닙니다. 미평가는 위험 없음과 다릅니다.',
                            '원문 날짜가 30일보다 오래되면 과거 근거로 표시하며, 날짜가 없으면 최신성을 확인할 수 없습니다.'],
            'sources': [{'title': 'NIST AI RMF Measure', 'url': 'https://airc.nist.gov/airmf-resources/playbook/measure/'},
                        {'title': 'NIST AI RMF Manage', 'url': 'https://airc.nist.gov/airmf-resources/playbook/manage/'}]}
