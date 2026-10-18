"""Independent one-repair chains over the same frozen sources and checkpoints."""


def risk(service, run_id, evidence, report, audit, context, require_all_news):
    from strategic_workflow import digest
    from risk_analysis import RISK_SCHEMA, validate_risk_report
    revised = report
    if not audit['accepted'] or audit.get('evidence_hash') != digest(evidence):
        revised = service._stage(run_id, 'risk_revision', lambda: service._revise(
            'risk_revision', evidence, report, audit, dict(context, risk_report=report, critique=audit),
            RISK_SCHEMA, lambda value: validate_risk_report(value, evidence), risk=True))
    checked = service._stage(run_id, 'risk_reverification',
        lambda: dict(audit, reused=True, reuse_reason='unchanged_evidence_report_and_verification_version')
        if service._reusable_risk_audit(audit, evidence, revised, require_all_news)
        else service._audit(evidence, revised, risk=True, require_all_news=require_all_news))
    return revised, service._current_audit(checked, evidence, revised,
        required_checked=revised.get('not_assessable_evidence_ids', []) if require_all_news else ())


def strategy(service, run_id, evidence, report, audit, context, require_all_news, deliberation, request):
    from strategic_workflow import REPORT
    from event_observations import report_schema
    if service._reusable_strategy_audit(audit, evidence, report, require_all_news, deliberation):
        checked = service._stage(run_id, 'strategy_audit_reuse', lambda: dict(audit, reused=True,
            reuse_reason='unchanged_evidence_report_and_verification_version'))
        return report, checked
    revised = service._stage(run_id, 'revision', lambda: service._revise(
        'revision', evidence, report, audit, dict(context, report=report, critique=audit),
        report_schema(REPORT, evidence), lambda value: service._report(value, evidence),
        escalation=request.get('completion_attempt', 1) >= 2))
    checked = service._stage(run_id, 'reverification', lambda: service._audit(
        evidence, revised, require_all_news=require_all_news, deliberation=deliberation))
    return revised, service._current_audit(checked, evidence, revised, deliberation=deliberation)
