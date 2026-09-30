"""Two-call single-document path with the existing evidence/admission contracts."""
import json
import re
from workflow_efficiency import cached_call
from model_policy import policy

VERSION = 'compact-v1'
# Conservative routing; absence of these terms is not a declaration of safety.
COMPLEX = re.compile(r'벤치마크|benchmark|contaminat|오염|상충|논문|초록|연구 결과|실험 결과|정확도|비교 연구|독파모|소송|특허|논란|취약|침해|security|clinical|arxiv', re.I)
SENSITIVE = re.compile(r'의료|임상|환자|전쟁|군사|핵무기|사망|인명|취약점|랜섬|해킹|개인정보|국가안보|clinical|patient|warfare|vulnerability', re.I)


# Publication nouns alone do not imply comparative scientific findings.
PUBLICATION = re.compile(r'논문|초록|arxiv', re.I)
ANNOUNCEMENT = re.compile(r'공개|출시|배포|출간|발표|출판|release|launch|publish|available', re.I)
FINDINGS = re.compile(r'실험|평가|성능|정확|비교|능가|향상|개선|효과|입증|증명|한계|실패|위험|편향|결과|제안|실증|효율|\d\s*%|experiment|evaluat|performance|accuracy|compar|outperform|improv|result|finding|risk|bias|limitation|propos|efficien|sota', re.I)


def content_route(evidence):
    text = ' '.join(e.get('text', '') for e in evidence)
    if SENSITIVE.search(text):
        return 'sensitive'
    if not COMPLEX.search(text):
        return 'simple_document'
    # Only relax publication nouns; every other existing exclusion still wins.
    without_publication = PUBLICATION.sub('', text)
    if COMPLEX.search(without_publication):
        return 'complex_evidence'
    prose = re.sub(r'https?://\S+', '', text)
    if len(text) <= 3000 and ANNOUNCEMENT.search(prose) and not FINDINGS.search(prose):
        return 'simple_publication_announcement'
    return 'research_findings_or_unclear'


def eligible(snapshot, request, enrichment):
    return (request.get('analysis_mode') == VERSION and len(snapshot) == 1
            and request.get('completion_attempt', 1) <= 1
            and not request.get('question') and not request.get('terms')
            and not (request.get('improvement_context') or {}).get('review_issues')
            and not (request.get('improvement_context') or {}).get('followup_tasks')
            and not (request.get('active_rules') or {}).get('rules')
            and not enrichment['coverage'].get('failed_urls')
            and all(e.get('origin') in ('telegram_excerpt', 'fetched_url_excerpt') for e in enrichment['evidence'])
            and sum(len(e.get('text', '')) for e in enrichment['evidence']) <= 8000
            and content_route(enrichment['evidence']) in ('simple_document', 'simple_publication_announcement'))


def prompt(service, role, evidence, payload):
    # Keep the established risk definitions and source-provenance instructions.
    base = service._prompt('risk_assessment' if role == 'integrated_analysis' else 'risk_verification', evidence, {})
    instructions = base.split('\nDATA:\n')[0].split('\n', 1)[1]
    mission = (
        '한 문서의 국가·기술 관점, 상충 근거와 불확실성을 함께 고려해 최종 전략 report와 risk_report를 작성한다. '
        '중간 역할 보고서나 가상의 합의는 만들지 않는다. report는 summary/claims/limitations/event_observations다. '
        'claims는 핵심 1~3개, 각 claim의 title/detail/category/evidence_ids/uncertainty를 작성한다. '
        'category는 opportunity/risk/watch_signal/strategic_concept이다. 모든 telegram_excerpt ID가 claim에 연결되어야 한다. '
        '자료가 부족하면 해당 근거의 한계를 watch_signal로 설명하며 사실을 발명하지 않는다. '
        'event_observations는 원문 문서별 최대 한 사건, 관측 행위가 없으면 빈 배열이다. '
        'actor/action/target/outcome/occurred_at/uncertainty와 actor_countries/affected_countries/evidence_ids를 원문에서만 추출한다. '
        '계획·기대는 실제 outcome이 아니며 국가·일자를 추측하지 않는다. 미상은 빈 문자열·빈 배열이다.'
        if role == 'integrated_analysis' else
        '별도 독립 검증자로 report와 risk_report를 현재 원문 전체와 대조한다. 생성자의 자체 평가는 검증 근거가 아니다. '
        'verification과 risk_verification 각각 accepted/issues/checked_evidence_ids/limitations를 작성한다. '
        '사실과 해석의 혼동, 누락된 중요한 반대 근거·조건·불확실성, 미지원 인과·등급은 거절한다. '
        '전략의 모든 인용 및 각 사건의 모든 필드·국가·일자를 확인하고 verification.checked_event_indices에 '
        '실제로 검토한 모든 사건 번호를 기록한다. 위험 평가 불가 분류도 모든 근거를 직접 대조한다. '
        '전략·위험 모두 accepted이고 issues가 비어 있을 때만 통과한다.'
    )
    # Evidence appears once; no model-produced summaries replace original evidence.
    return f'ROLE: {role}\n{mission}\n{instructions}\nDATA:\n' + json.dumps(
        dict(evidence=evidence, **payload), ensure_ascii=False, separators=(',', ':'))


def execute(service, run_id, evidence, request, enrichment):
    from strategic_workflow import REPORT, AUDIT, object_schema, evidence_schema, now
    from event_observations import report_schema, audit_schema
    from risk_analysis import RISK_SCHEMA, validate_risk_report
    schema = evidence_schema(object_schema({'report': report_schema(REPORT, evidence), 'risk_report': RISK_SCHEMA}), evidence)
    generation = prompt(service, 'integrated_analysis', evidence, {'coverage': enrichment['coverage']})

    def validate_generation(value):
        return {'report': service._report(value['report'], evidence),
                'risk_report': validate_risk_report(value['risk_report'], evidence)}

    generated = service._stage(run_id, 'integrated_analysis', lambda: cached_call(
        service, run_id, 'integrated_analysis', generation, schema, validate_generation),
        dependencies=[generation,schema,policy('integrated_analysis')])
    # Revalidate checkpoint artifacts too; never trust status alone.
    generated = validate_generation(generated)
    report, risk_report = generated['report'], generated['risk_report']
    review_schema = evidence_schema(object_schema({'verification': audit_schema(AUDIT, report['event_observations']),
                                                   'risk_verification': AUDIT}), evidence)
    critical = any(r.get('current_severity') in ('high', 'critical') for r in risk_report['risks'])

    def validate_review(value):
        return {'verification': service._validate_audit(value['verification'], evidence, report, require_all_news=True),
                'risk_verification': service._validate_audit(value['risk_verification'], evidence, risk_report, risk=True, require_all_news=True)}

    reviewed = service._stage(run_id, 'integrated_verification', lambda: cached_call(
        service, run_id, 'integrated_verification', prompt(service, 'integrated_verification', evidence, generated),
        review_schema, validate_review, escalation=critical),
        dependencies=[prompt(service, 'integrated_verification', evidence, generated),review_schema,policy('integrated_verification',escalation=critical)])
    audit = service._current_audit(reviewed['verification'], evidence, report)
    risk_audit = service._current_audit(reviewed['risk_verification'], evidence, risk_report,
                                      required_checked=risk_report['not_assessable_evidence_ids'])
    accepted = audit.get('accepted') is True and risk_audit.get('accepted') is True
    for stage, value in [('synthesis', report), ('risk_assessment', risk_report),
                         ('verification', audit), ('risk_verification', risk_audit)]:
        service._save(run_id, stage, value)
    final = dict(report=report, verification=audit, risk_report=risk_report, risk_verification=risk_audit,
                 verified=accepted, risk_verified=risk_audit.get('accepted') is True,
                 evidence=evidence, event_observations=report['event_observations'], coverage=enrichment['coverage'],
                 model_provenance=service._model_provenance(run_id), basis='snapshot_excerpt_analysis', completed_at=now(),
                 orchestration={'pattern': VERSION, 'generation_calls_max': 1, 'review_calls_max': 1,
                                'independent_role_reports': False,
                                'reused_stages': {stage: service._artifact(run_id, 'reuse_' + stage) for stage in ('integrated_analysis','integrated_verification') if service._artifact(run_id, 'reuse_' + stage)}, 'mirofish_simulation_executed': False})
    from graph_rag import validated_workflow_content
    from risk_analysis import validated_risk_content
    if accepted and not (validated_workflow_content(final, run_id) and validated_risk_content(final, run_id)):
        accepted = False
        audit = dict(audit, accepted=False, issues=list(audit.get('issues', [])) + ['공개 근거 계약을 만족하지 않습니다.'])
        final.update(verified=False, verification=audit)
        service._save(run_id, 'verification', audit)
    service._save(run_id, 'final', final)
    from review_routing import route_review
    service._save(run_id, 'review_plan', route_review({'id': run_id, 'results': final}))
    # Rejection is preserved for the existing bounded completion retry policy.
    service._status(run_id, 'complete' if accepted else 'needs_review')
