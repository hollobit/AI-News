"""Document-level admission for complete strategic and risk review workflows."""
from link_groups import canonical_url
from recursive_improvement import quality_metrics, news_fingerprint


def message_text(item):
    return '\n'.join(str(item.get(k) or '') for k in ('title', 'summary', 'text', 'description')).strip()[:2000]


def document_admission(run, item, *, require_current_source=True):
    result = run.get('results') or {}
    evidence = result.get('evidence') or []
    url = canonical_url(item.get('source_url') or '')
    matching = [e for e in evidence if e.get('origin') == 'telegram_excerpt'
                and canonical_url(e.get('url') or '') == url and e.get('text') == message_text(item)]
    sources = [e for e in evidence if e.get('origin') == 'fetched_url_excerpt'
               and url and canonical_url(e.get('url') or '') == url]
    own_ids = {e['id'] for e in matching + sources}
    refs = {ref for claim in (result.get('report') or {}).get('claims', []) for ref in claim.get('evidence_ids', [])}
    source = item.get('source_context') or {}
    source_text = str(source.get('text') or '')[:3500] if source.get('status') == 'fetched' else ''
    current_source = ((not source_text and not sources)
                      or bool(source_text and sources and all(e.get('text') == source_text for e in sources)))
    current = bool(matching) and (not require_current_source or current_source)
    quality = quality_metrics(run)
    risk = result.get('risk_report') or {}
    checked = set((result.get('risk_verification') or {}).get('checked_evidence_ids') or [])
    assessed = set(risk.get('assessed_evidence_ids') or []) & checked
    unassessable = set(risk.get('not_assessable_evidence_ids') or []) & checked
    # Exact message evidence is required even when its URL has been reused.
    cited = bool(own_ids & refs)
    strategic = current and cited and quality['verified']
    risk_assessed = bool(current and quality['risk_verified'] and own_ids & assessed)
    risk_reviewed = bool(current and quality['risk_verified'] and own_ids & (assessed | unassessable))
    issues = list((result.get('verification') or {}).get('issues') or [])
    issues += list((result.get('risk_verification') or {}).get('issues') or [])
    if not current: issues.append('현재 문서 문맥 또는 URL 발췌와 분석 스냅샷이 일치하지 않습니다.')
    if not cited: issues.append('이 문서가 전략 보고서의 실제 인용에서 누락되었습니다.')
    if not risk_reviewed: issues.append('이 문서의 위험 평가 또는 평가 불가 분류에 대한 독립 대조가 미완료입니다.')
    if run.get('error'): issues.append(run['error'])
    return {'status': 'verified' if strategic else 'failed' if run.get('status') == 'failed' else 'needs_review',
            'verified': bool(strategic), 'risk_assessed': risk_assessed, 'risk_reviewed': risk_reviewed,
            'complete': bool(strategic and risk_reviewed), 'current_snapshot': current,
            'workflow_run_id': run.get('id'), 'issues': list(dict.fromkeys(issues))}


def observed_item(item, run):
    """Include genuinely retrieved evidence in the version cursor after a run."""
    url = canonical_url(item.get('source_url') or '')
    sources = [e for e in (run.get('results') or {}).get('evidence', [])
               if e.get('origin') == 'fetched_url_excerpt' and url and canonical_url(e.get('url') or '') == url]
    return dict(item, source_context={'status': 'fetched', 'text': sources[-1]['text']}) if sources else item
