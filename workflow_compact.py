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
    return (request.get('analysis_mode') in (VERSION,'adaptive-v2') and len(snapshot) == 1
            and request.get('completion_attempt', 1) <= 1
            and not request.get('question') and not request.get('terms')
            and not (request.get('improvement_context') or {}).get('review_issues')
            and not (request.get('improvement_context') or {}).get('followup_tasks')
            and not (request.get('active_rules') or {}).get('rules')
            and not enrichment['coverage'].get('failed_urls')
            and all(e.get('origin') in ('telegram_excerpt', 'fetched_url_excerpt') for e in enrichment['evidence'])
            and sum(len(e.get('text', '')) for e in enrichment['evidence']) <= 8000
            and content_route(enrichment['evidence']) in ('simple_document', 'simple_publication_announcement'))


def prompt(service, role, evidence, payload, *, extra_instructions=""):
    # Keep the established risk definitions and source-provenance instructions.
    base = service._prompt('risk_assessment' if role == 'integrated_analysis' else 'risk_verification', evidence, {})
    instructions = base.split('\nDATA:\n')[0].split('\n', 1)[1]
    mission = (
        '한 문서의 국가·기술 관점, 상충 근거와 불확실성을 함께 고려해 최종 전략 report와 risk_report를 작성한다. '
        '중간 역할 보고서나 가상의 합의는 만들지 않는다. report는 summary/claims/limitations/event_observations다. '
        'claims는 핵심 1~3개, 각 claim의 title/detail/category/evidence_ids/uncertainty를 작성한다. '
        'category는 opportunity/risk/watch_signal/strategic_concept이다. 모든 telegram_excerpt ID가 claim에 연결되어야 한다. '
        '자료가 부족하면 해당 근거의 한계를 watch_signal로 설명하며 사실을 발명하지 않는다. '
        '위험 근거 분류에서 읽고 확인했다는 사실은 assessed를 뜻하지 않는다. 피해·노출·발생 지표나 시나리오 조건이 없어 위험을 평가할 수 없는 출시·공개 안내는 not_assessable_evidence_ids에 넣는다. limitations에서 평가할 정보가 부족하다고 설명한 동일 근거를 assessed_evidence_ids에 넣지 않는다. 평가 불가는 안전함의 확정이 아니다. '
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
    return f'ROLE: {role}\n{mission}\n{instructions}\n{extra_instructions}\nDATA:\n' + json.dumps(
        dict(evidence=evidence, **payload), ensure_ascii=False, separators=(',', ':'))


def execute(service, run_id, evidence, request, enrichment):
    from strategic_workflow import REPORT, object_schema, evidence_schema
    from event_observations import report_schema
    from risk_analysis import RISK_SCHEMA, validate_risk_report
    schema = evidence_schema(object_schema({'report': report_schema(REPORT, evidence), 'risk_report': RISK_SCHEMA}), evidence)
    generation = prompt(service, 'integrated_analysis', evidence, {'coverage': enrichment['coverage']})

    def validate_generation(value):
        return {'report': service._report(value['report'], evidence),
                'risk_report': validate_risk_report(value['risk_report'], evidence)}

    generated = service._stage(run_id, 'integrated_analysis', lambda: cached_call(
        service, run_id, 'integrated_analysis', generation, schema, validate_generation),
        dependencies=[generation,schema,policy('integrated_analysis',prompt=generation)])
    # Revalidate checkpoint artifacts too; never trust status alone.
    generated = validate_generation(generated)
    report, risk_report = generated['report'], generated['risk_report']
    from workflow_joint import finish
    finish(service,run_id,evidence,request,enrichment,report,risk_report,pattern=VERSION,normal_calls=2)
