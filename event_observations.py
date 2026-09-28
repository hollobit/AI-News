"""Source-grounded event observations extracted inside the existing synthesis call."""
import copy
from link_groups import canonical_url
FIELDS=('actor','action','target','outcome','occurred_at','uncertainty')
def document_key(entry):
    """Compare source identities, retaining separate URL-free article IDs."""
    return canonical_url(entry.get('url') or '') or entry['id']
def report_schema(base,evidence):
    result=copy.deepcopy(base)
    props={name:{'type':'string'} for name in FIELDS}
    props.update({name:{'type':'array','items':{'type':'string'}} for name in ('actor_countries','affected_countries','evidence_ids')})
    maximum=len({document_key(entry) for entry in evidence})
    result['properties']['event_observations']={'type':'array','maxItems':min(24,maximum),'items':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}
    result['required'].append('event_observations')
    return result

def validate_events(events,evidence):
    if not isinstance(events,list) or len(events)>24:raise ValueError('사건 목록 형식 오류')
    allowed={entry['id'] for entry in evidence};byid={entry['id']:entry for entry in evidence};seen=set()
    for event in events:
        if not isinstance(event,dict) or any(not isinstance(event.get(key),str) for key in FIELDS):raise ValueError('사건 필드 형식 오류')
        for key in ('actor_countries','affected_countries','evidence_ids'):
            if not isinstance(event.get(key),list) or any(not isinstance(value,str) for value in event[key]):raise ValueError('사건 인용·국가 형식 오류')
        refs=event['evidence_ids']
        if not refs or not set(refs)<=allowed:raise ValueError('사건의 실제 원문 인용이 누락되었습니다.')
        if not event['action'].strip():raise ValueError('관측된 행위 없는 빈 사건은 목록에 추가할 수 없습니다.')
        documents={document_key(byid[ref]) for ref in refs}
        if len(documents)!=1 or seen&documents:raise ValueError('사건은 문서별 하나씩 동일 원문에 연결해야 합니다.')
        seen.update(documents)
    return events

def audit_schema(base,events):
    result=copy.deepcopy(base)
    # Empty arrays require an explicit empty reviewed list without an invalid empty enum.
    item={'type':'integer','minimum':0}
    if events:item['enum']=list(range(len(events)))
    result['properties']['checked_event_indices']={'type':'array','items':item,'maxItems':len(events)}
    result['required'].append('checked_event_indices')
    return result

def events_audit_issues(audit,events,evidence):
    issues=[]
    try:validate_events(events,evidence)
    except (ValueError,TypeError,KeyError) as exc:issues.append(str(exc));return issues
    indices=audit.get('checked_event_indices')
    if not isinstance(indices,list) or any(type(index) is not int for index in indices) or sorted(indices)!=list(range(len(events))):issues.append('모든 사건의 행위·대상·결과·일자·국가 귀속 독립 대조가 누락되었습니다.')
    refs={ref for event in events for ref in event['evidence_ids']}
    if not refs<=set(audit.get('checked_evidence_ids') or []):issues.append('사건의 모든 원문 근거 대조가 누락되었습니다.')
    return issues
