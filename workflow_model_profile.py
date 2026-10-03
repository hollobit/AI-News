"""Small-model admission for short factual announcements, before wire compaction."""
import json
import re


def simple_announcement(prompt):
    if not prompt.startswith('ROLE: integrated_analysis\n'):return False
    _,sep,body=prompt.partition('\nDATA:\n')
    if not sep:return False
    try:data=json.loads(body)
    except (ValueError,TypeError):return False
    if not isinstance(data,dict):return False
    evidence=data.get('evidence');coverage=data.get('coverage')
    if not isinstance(evidence,list) or not evidence or not isinstance(coverage,dict) or coverage.get('failed_urls'):return False
    if any(not isinstance(e,dict) or e.get('origin') not in ('telegram_excerpt','fetched_url_excerpt') for e in evidence):return False
    if sum(e['origin']=='telegram_excerpt' for e in evidence)!=1:return False
    text=' '.join(str(e.get('text') or '') for e in evidence)
    if not text or len(text)>800:return False
    from workflow_compact import ANNOUNCEMENT,COMPLEX,SENSITIVE,FINDINGS
    from workflow_complex import RESEARCH_CONFLICT,AGENT_CONTROL
    prose=re.sub(r'https?://\S+','',text)
    return bool(ANNOUNCEMENT.search(prose) and not any(pattern.search(prose) for pattern in
        (COMPLEX,SENSITIVE,FINDINGS,RESEARCH_CONFLICT,AGENT_CONTROL)))
