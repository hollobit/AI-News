"""Shared independent joint review and one bounded repair of rejected reports only."""
from concurrent.futures import ThreadPoolExecutor
from workflow_compact import prompt as compact_prompt


def finish(service,run_id,evidence,request,enrichment,report,risk_report,*,pattern,normal_calls):
    from strategic_workflow import REPORT,AUDIT,object_schema,evidence_schema,now
    from event_observations import report_schema,audit_schema as event_audit_schema
    from risk_analysis import RISK_SCHEMA,validate_risk_report,validated_risk_content
    from graph_rag import validated_workflow_content
    from workflow_patch import targets,audit_schema as patch_audit_schema
    from model_policy import policy
    context={'coverage':enrichment['coverage'],'request':request}
    strategy_schema=report_schema(REPORT,evidence)
    validate_strategy=lambda value:service._report(value,evidence)
    validate_risk=lambda value:validate_risk_report(value,evidence)
    def review(report,risk_report,stage):
        generated={'report':report,'risk_report':risk_report,'coverage':enrichment['coverage'],
            'prior_feedback':request.get('improvement_context') or {},
            'strategy_target_map':{key:index for key,(index,_) in targets(report).items()},
            'risk_target_map':{key:index for key,(index,_) in targets(risk_report,True).items()}}
        prompt=compact_prompt(service,'integrated_verification',evidence,generated,extra_instructions=
            '\n각 검토의 issues를 issue_index(0부터)로 연결하여 수정할 target_id와 fields를 revision_targets에 지정한다. '
            '전역 누락·구조 문제나 지적이 없으면 빈 배열이다. 중요한 위험·반대 근거가 두 보고서를 합친 결과에서 누락되거나 서로 모순되는지 확인한다. 위험보고서의 모든 시나리오를 전략보고서에 중복 기재하도록 요구하지 않는다.')
        schema=evidence_schema(object_schema({
            'verification':patch_audit_schema(event_audit_schema(AUDIT,report['event_observations']),report),
            'risk_verification':patch_audit_schema(AUDIT,risk_report,True)}),evidence)
        critical=any(r.get('current_severity') in ('high','critical') for r in risk_report['risks'])
        def validate(value):
            return {'verification':service._validate_audit(value['verification'],evidence,report,require_all_news=True),
                'risk_verification':service._validate_audit(value['risk_verification'],evidence,risk_report,risk=True,require_all_news=True)}
        saved=service._stage(run_id,stage,lambda:service._validated_call(stage,prompt,schema,validate,escalation=critical),
            dependencies=[prompt,schema,policy('integrated_verification',escalation=critical)])
        return validate(saved)

    audits=review(report,risk_report,'integrated_verification')
    original_audits=audits
    if not all(audits[key]['accepted'] for key in ('verification','risk_verification')):
        # One bounded repair; accepted fields survive, all final outputs are reviewed again.
        def repair_strategy():
            if audits['verification']['accepted']:return report
            return service._stage(run_id,'revision',lambda:service._revise('revision',evidence,report,audits['verification'],
                dict(context,report=report,critique=audits['verification']),strategy_schema,validate_strategy),
                dependencies=[report,audits['verification'],evidence,policy('revision')])
        def repair_risk():
            if audits['risk_verification']['accepted']:return risk_report
            return service._stage(run_id,'risk_revision',lambda:service._revise('risk_revision',evidence,risk_report,audits['risk_verification'],
                dict(context,risk_report=risk_report,critique=audits['risk_verification']),RISK_SCHEMA,validate_risk,risk=True),
                dependencies=[risk_report,audits['risk_verification'],evidence,policy('risk_revision')])
        with ThreadPoolExecutor(max_workers=2) as pool:
            strategy_repair=pool.submit(repair_strategy)
            risk_repair=pool.submit(repair_risk)
            revised_strategy,revised_risk=strategy_repair.result(),risk_repair.result()
        report,risk_report=revised_strategy,revised_risk
        audits=review(report,risk_report,'integrated_reverification')
    audit=service._current_audit(audits['verification'],evidence,report)
    risk_audit=service._current_audit(audits['risk_verification'],evidence,risk_report,required_checked=risk_report['not_assessable_evidence_ids'])
    accepted=audit.get('accepted') is True and risk_audit.get('accepted') is True
    final=dict(report=report,risk_report=risk_report,verification=audit,risk_verification=risk_audit,
        verified=accepted,risk_verified=risk_audit.get('accepted') is True,evidence=evidence,
        event_observations=report['event_observations'],coverage=enrichment['coverage'],
        model_provenance=service._model_provenance(run_id),basis='snapshot_excerpt_analysis',completed_at=now(),
        orchestration={'pattern':pattern,'normal_path_calls':normal_calls,'max_repair_cycles':1,'independent_role_reports':False,
            'separate_risk_generation':pattern=='parallel-drafts-v1','initial_audits':original_audits,
            'reused_stages':{key:value for key,value in service.get_run(run_id)['artifacts'].items() if key.startswith('reuse_')},
            'mirofish_simulation_executed':False})
    if accepted and not (validated_workflow_content(final,run_id) and validated_risk_content(final,run_id)):
        accepted=False
        audit=dict(audit,accepted=False,issues=audit.get('issues',[])+['공개 근거 계약을 만족하지 않습니다.'])
        final.update(verified=False,verification=audit)
    for stage,value in [('synthesis',report),('risk_assessment',risk_report),('verification',audit),('risk_verification',risk_audit),('final',final)]:
        service._save(run_id,stage,value)
    from review_routing import route_review
    service._save(run_id,'review_plan',route_review({'id':run_id,'results':final}))
    service._status(run_id,'complete' if accepted else 'needs_review')
