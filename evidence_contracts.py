"""Shared stored-review integrity, with explicit domain-specific admission above it."""


def reviewed_payload(audit, report, evidence, digest, *, exact_issues=False):
    """Do not admit stale inputs, citations or publication: callers check those.

    The helper only proves that the stored independent review describes exactly
    this report/evidence. Preserve each domain's existing empty-issues policy.
    """
    if not isinstance(audit,dict):return False
    clean=audit.get('issues')==[] if exact_issues else not audit.get('issues')
    return bool(audit.get('accepted') is True and clean
                and audit.get('report_hash')==digest(report)
                and audit.get('evidence_hash')==digest(evidence))
