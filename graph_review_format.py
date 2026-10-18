"""Lossless grouped review output; every claim, criterion and axis stays explicit.

Only the wire representation changes. Canonical reviews, source context and
independent acceptance rules remain unchanged, including on repair/recheck.
"""
from copy import deepcopy

from graph_answer_quality import CRITERIA
from strategic_jobs import obj

INSTRUCTIONS='''
출력 형식만 다음처럼 묶는다. 검토 기준·원문 범위·자기 인용 기준은 위와 같다.
support/reject는 checks의 supported=true/false 목록이다. review_claim_indices의 모든 번호를 두 목록 중 정확히 한 곳에 명시한다.
criteria_pass/criteria_fail은 quality.criteria의 passed=true/false 목록이다. 기준 번호는 0=direct_answer, 1=coverage, 2=explanation, 3=integration, 4=scope다.
이 다섯 기준을 각각 독립 판단하고 모든 번호를 두 목록 중 정확히 한 곳에 명시한다.
addressed/gaps/missing은 quality.axes의 addressed/evidence_gap/missing 목록이다. 모든 axis_index를 세 목록 중 정확히 한 곳에 명시한다.
reject에는 claim_index/reason, criteria_fail에는 criterion_index/reason, gaps/missing에는 axis_index/reason을 적는다.
통과/설명 완료 번호는 정수 배열이고, 실패·공백 사유는 위 기준대로 구체적으로 적는다. 누락된 번호를 통과로 간주하지 않는다.
answer_supported, missing_information, citation_corrections의 의미는 같다. 간단한 출력이 검토 생략을 뜻하지 않는다.
'''

COMMON=('answer_supported','missing_information','citation_corrections')
FIELDS={'support','reject','criteria_pass','criteria_fail','addressed','gaps','missing',*COMMON}


def dimensions(original):
    props=original['properties']
    if set(props['quality']['properties']['criteria']['properties'])!=set(CRITERIA):
        raise RuntimeError('독립 검토 기준과 출력 형식이 일치하지 않습니다.')
    requests=props['checks']['items']['properties']['claim_index'].get('enum',[])
    axes=props['quality']['properties']['axes']['items']['properties']['axis_index']['enum']
    return requests,axes


def schema(original):
    requests,axes=dimensions(original)
    def numbers(values):
        return dict(type='array',maxItems=len(values),items=dict(type='integer',**({'enum':values} if values else {})))
    def reasons(field,values):
        return dict(type='array',maxItems=len(values),items=obj({
            field:dict(type='integer',**({'enum':values} if values else {})),
            'reason':{'type':'string'}}))
    criteria=list(range(len(CRITERIA)))
    return obj(dict(support=numbers(requests),reject=reasons('claim_index',requests),
        criteria_pass=numbers(criteria),criteria_fail=reasons('criterion_index',criteria),
        addressed=numbers(axes),gaps=reasons('axis_index',axes),missing=reasons('axis_index',axes),
        **{key:deepcopy(original['properties'][key]) for key in COMMON}))


def partition(passed,failed,field,values):
    if not isinstance(passed,list) or any(type(i) is not int for i in passed):
        raise RuntimeError('검토 통과 번호가 올바르지 않습니다.')
    if (not isinstance(failed,list) or any(not isinstance(v,dict) or set(v)!={field,'reason'}
            or type(v[field]) is not int or not isinstance(v['reason'],str) or not v['reason'].strip() for v in failed)):
        raise RuntimeError('검토 거절 사유가 올바르지 않습니다.')
    if sorted(passed+[v[field] for v in failed])!=sorted(values):
        raise RuntimeError('검토 대상 누락·중복이 있습니다.')


def restore(raw,original):
    # Retained canonical reviews and deterministic replay readers still use
    # the original format. Both routes pass the same downstream validation.
    if isinstance(raw,dict) and set(raw)==set(original['properties']):return raw
    if not isinstance(raw,dict) or set(raw)!=FIELDS:
        raise RuntimeError('검토 출력 필드가 완결되지 않았습니다.')
    requests,axes=dimensions(original)
    partition(raw['support'],raw['reject'],'claim_index',requests)
    partition(raw['criteria_pass'],raw['criteria_fail'],'criterion_index',range(len(CRITERIA)))
    if not isinstance(raw['gaps'],list) or not isinstance(raw['missing'],list):
        raise RuntimeError('질문 축 검토가 올바르지 않습니다.')
    partition(raw['addressed'],raw['gaps']+raw['missing'],'axis_index',axes)
    checks=[dict(claim_index=i,supported=True,reason=None) for i in raw['support']]
    checks += [dict(v,supported=False) for v in raw['reject']]
    criteria={CRITERIA[i]:dict(passed=True,reason=None) for i in raw['criteria_pass']}
    criteria.update({CRITERIA[v['criterion_index']]:dict(passed=False,reason=v['reason']) for v in raw['criteria_fail']})
    axis=[dict(axis_index=i,status='addressed',reason=None) for i in raw['addressed']]
    axis += [dict(v,status='evidence_gap') for v in raw['gaps']]+[dict(v,status='missing') for v in raw['missing']]
    return dict(checks=sorted(checks,key=lambda v:v['claim_index']),
        quality=dict(criteria=criteria,axes=sorted(axis,key=lambda v:v['axis_index'])),
        **{key:deepcopy(raw[key]) for key in COMMON})
