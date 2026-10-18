"""One conservative routing decision for every workflow entry point."""

def select(snapshot, request, enrichment, previous=None):
    from workflow_compact import eligible, content_route
    from workflow_complex import eligible as complex_eligible, VERSION
    route=content_route(enrichment['evidence'])
    partial=bool(enrichment['coverage'].get('failed_urls'))
    if eligible(snapshot,request,enrichment):
        path,reason='compact-v1','available_news_excerpt' if partial else route
    elif complex_eligible(snapshot,request,enrichment):
        from workflow_recovery_route import operational_feedback_only
        path,reason=VERSION,('available_news_excerpt' if partial else
                            'operational_feedback_revalidated' if operational_feedback_only(request) else 'eligible_parallel_drafts')
    else:
        path='multi-role'
        if request.get('analysis_mode') not in ('adaptive-v2','compact-v1'):reason='legacy_request'
        elif any(e.get('origin') not in ('telegram_excerpt','fetched_url_excerpt') for e in enrichment['evidence']):reason='source_contract'
        elif enrichment['coverage'].get('failed_urls'):reason='source_unavailable'
        elif request.get('completion_attempt',1)>1:reason='review_retry'
        else:reason='sensitive_complex_or_custom_scope'
    if path!='multi-role' and partial and previous and previous.get('path')=='multi-role' and previous.get('reason')=='source_unavailable':
        # Resume the saved role/checkpoint graph; shorter routes apply to new
        # runs, not to unfinished independent analyst stages.
        return dict(previous)
    plan=dict(path=path,reason=reason,content_route=route,routing_version='adaptive-v8')
    if path=='multi-role':
        from workflow_synthesis import eligible as parallel_synthesis, VERSION as schedule
        if parallel_synthesis(snapshot,request,enrichment):plan['synthesis_schedule']=schedule
    return plan
