"""Three-call route: strategy and risk drafts in parallel, one independent joint review."""
import re
from concurrent.futures import ThreadPoolExecutor
from workflow_compact import SENSITIVE

VERSION = 'parallel-drafts-v1'
# Paired evaluation found loss of a distinct contamination scenario; keep this class on the original path.
RESEARCH_CONFLICT = re.compile(r'contamin|오염|상충|contradict|conflicting|mixed findings',re.I)
AGENT_CONTROL = re.compile(r'우회|bypass|기만|decept|unauthoriz|무단|정보 유출',re.I)


def eligible(snapshot, request, enrichment):
    evidence=enrichment['evidence']
    from workflow_recovery_route import operational_feedback_only
    recovered=operational_feedback_only(request)
    return (request.get('analysis_mode')=='adaptive-v2' and len(snapshot)==1
            and (request.get('completion_attempt',1)==1 or recovered)
            and not request.get('question') and not request.get('terms')
            and not (request.get('active_rules') or {}).get('rules')
            and (recovered or not (request.get('improvement_context') or {}).get('review_issues'))
            and (recovered or not (request.get('improvement_context') or {}).get('followup_tasks'))
            and not enrichment['coverage'].get('failed_urls')
            and all(e.get('origin') in ('telegram_excerpt','fetched_url_excerpt') for e in evidence)
            and sum(len(e.get('text','')) for e in evidence)<=14000
            and not SENSITIVE.search(' '.join(e.get('text','') for e in evidence))
            and not RESEARCH_CONFLICT.search(' '.join(e.get('text','') for e in evidence))
            and not AGENT_CONTROL.search(' '.join(e.get('text','') for e in evidence)))


def execute(service,run_id,evidence,request,enrichment):
    from strategic_workflow import REPORT,evidence_schema
    from event_observations import report_schema
    from risk_analysis import RISK_SCHEMA,validate_risk_report
    from model_policy import policy
    context={'coverage':enrichment['coverage'],'request':request}
    strategy_schema=report_schema(REPORT,evidence)
    generation=service._prompt('synthesis',evidence,context,output_role='strategy_draft',extra_instructions=
        '이 경로에서는 국가·기술 관점을 직접 종합한 최종 전략 초안을 작성한다. 별도 역할 보고서·합의는 존재하지 않는다. '
        '원문에 명시된 비교 조건·반대 근거·불확실성을 보존한다. 원문에 없는 일반적인 우려나 후속 과제로 빈 절을 채우지 않는다. 위험 평가는 별도 병렬 작업과 독립 검토에서 수행된다.')
    risk_prompt=service._prompt('risk_assessment',evidence,context)
    validate_strategy=lambda value:service._report(value,evidence)
    validate_risk=lambda value:validate_risk_report(value,evidence)
    def draft(stage,prompt,schema,validate,role):
        return service._stage(run_id,stage,lambda:service._validated_call(stage,prompt,evidence_schema(schema,evidence),validate),
            dependencies=[prompt,schema,policy(role)])
    with ThreadPoolExecutor(max_workers=2) as pool:
        strategy_future=pool.submit(draft,'strategy_draft',generation,strategy_schema,validate_strategy,'strategy_draft')
        risk_future=pool.submit(draft,'risk_assessment',risk_prompt,RISK_SCHEMA,validate_risk,'risk_assessment')
        report,risk_report=validate_strategy(strategy_future.result()),validate_risk(risk_future.result())

    from workflow_joint import finish
    finish(service,run_id,evidence,request,enrichment,report,risk_report,pattern=VERSION,normal_calls=3)
