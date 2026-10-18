"""Draft from sources in parallel; actual independent role differences still gate review."""

VERSION = 'parallel-source-synthesis-v1'
INSTRUCTIONS = (
    '이 초안은 다른 역할 보고서가 도착하기 전에 현재 원문만으로 작성한다. 국가·기술 관점과 기회·위험·관찰 신호를 원문의 범위에서 직접 검토하되 제공되지 않은 분석가 의견·합의·이견을 만들지 않는다. '
    '뒤따르는 독립 검토는 별도로 생성된 실제 국가·기술 의견과 중요 이견까지 모두 대조한다. 초안을 완료 판정이나 자체 검증으로 취급하지 않는다. 원문에 없는 정책 처방과 상황을 발명하지 않는다.'
)


def eligible(snapshot, request, enrichment):
    return (request.get('synthesis_schedule') == VERSION
            and request.get('analysis_mode') == 'adaptive-v2' and request.get('completion') is True
            and len(snapshot) == 1 and request.get('completion_attempt', 1) <= 1
            and not request.get('question') and not request.get('terms')
            and not (request.get('improvement_context') or {}).get('review_issues')
            and not (request.get('improvement_context') or {}).get('followup_tasks')
            and not (request.get('active_rules') or {}).get('rules')
            and not enrichment['coverage'].get('failed_urls')
            and all(e.get('origin') in ('telegram_excerpt', 'fetched_url_excerpt') for e in enrichment['evidence'])
            and sum(len(e.get('text', '')) for e in enrichment['evidence']) <= 8000)


def draft(service, run_id, evidence, request, enrichment):
    from strategic_workflow import REPORT, evidence_schema
    from event_observations import report_schema
    from model_policy import policy
    schema=evidence_schema(report_schema(REPORT,evidence),evidence)
    prompt=service._prompt('synthesis',evidence,{'coverage':enrichment['coverage'],'request':request},
                           extra_instructions=INSTRUCTIONS)
    return service._stage(run_id,'synthesis',lambda:service._validated_call('synthesis',
        prompt,schema,lambda value:service._report(value,evidence)),
        dependencies=[VERSION,prompt,schema,policy('synthesis',prompt=prompt)])
