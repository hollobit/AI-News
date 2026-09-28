"""Keep actual role positions visible; shared evidence is not a contradiction verdict."""
import copy
import hashlib
import json


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def build_deliberation(reports,evidence):
    allowed={entry['id'] for entry in evidence};positions=[]
    for role,report in reports.items():
        for claim in report.get('claims',[]):
            refs=claim.get('evidence_ids') or []
            if not refs or not set(refs)<=allowed:raise ValueError('역할 입장의 근거 인용 오류')
            positions.append({'id':'position:'+digest([role,claim])[:20],'role':role,'title':claim['title'],
                'position':claim['detail'],'category':claim['category'],'supporting_evidence_ids':refs,
                'uncertainty':claim['uncertainty'],'change_conditions':[],
                'condition_status':'판단 변경 조건은 독립 검토에서 제안·관측을 구분해 보완합니다.'})
    grouped={}
    for ref in sorted(allowed):
        selected=[position for position in positions if ref in position['supporting_evidence_ids']]
        if len({position['role'] for position in selected})<2 or len({position['position'] for position in selected})<2:continue
        position_ids=sorted(position['id'] for position in selected)
        key=tuple(position_ids)
        if key in grouped:grouped[key]['evidence_ids'].append(ref)
        else:grouped[key]={'id':'difference:'+digest(position_ids)[:20],
            'position_ids':position_ids,'evidence_ids':[ref],
            'status':'potential_difference','basis':'같은 근거에 대한 실제 역할 표현의 차이이며 모순은 독립 검토로 판정합니다.'}
    questions=list(grouped.values())
    return {'version':'deliberation-v1','positions':positions,'questions':questions,'source_snapshot_hash':digest(evidence),
            'basis':'역할 의견은 별도 확인 출처가 아닙니다. 다수결로 사실을 확정하지 않습니다.'}


def audit_schema(base,deliberation):
    schema=copy.deepcopy(base);ids=[question['id'] for question in deliberation['questions']]
    if not ids:return schema
    fields={'difference_id':{'type':'string','enum':ids},'material':{'type':'boolean'},
        'classification':{'type':'string','enum':['compatible','contradiction','different_scope','uncertain']},
        'preserved':{'type':'boolean'},'reason':{'type':'string'},
        'opposing_evidence_ids':{'type':'array','items':{'type':'string'}},
        'change_conditions':{'type':'array','items':{'type':'string'}}}
    schema['properties']['deliberation_checks']={'type':'array','items':{'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}}
    schema['required'].append('deliberation_checks');return schema


def validate_checks(audit,deliberation,evidence):
    expected={question['id'] for question in deliberation.get('questions',[])}
    if not expected:return []
    checks=audit.get('deliberation_checks');allowed={entry['id'] for entry in evidence}
    issues=[]
    if not isinstance(checks,list):return ['역할 간 중요 이견의 독립 대조가 누락되었습니다.']
    found=[]
    for check in checks:
        if not isinstance(check,dict):issues.append('이견 검토 형식 오류');continue
        found.append(check.get('difference_id'))
        if check.get('difference_id') not in expected or type(check.get('material')) is not bool or type(check.get('preserved')) is not bool or check.get('classification') not in ('compatible','contradiction','different_scope','uncertain') or not isinstance(check.get('reason'),str):issues.append('이견 검토 형식 오류')
        if not isinstance(check.get('opposing_evidence_ids'),list) or not set(check.get('opposing_evidence_ids',[]))<=allowed:issues.append('반대 근거 ID 오류')
        if not isinstance(check.get('change_conditions'),list) or any(not isinstance(value,str) for value in check.get('change_conditions',[])):issues.append('판단 변경 조건 형식 오류')
        if check.get('material') is True and check.get('preserved') is not True:issues.append('중요 역할 이견이 종합에서 누락되었습니다: '+str(check.get('difference_id')))
    if set(found)!=expected or len(found)!=len(expected):issues.append('모든 역할 차이를 정확히 한 번씩 검토하지 않았습니다.')
    return list(dict.fromkeys(issues))
