import copy

import pytest

from graph_answer_quality import CRITERIA, assess, review_schema, sufficient
from graph_review_format import restore, schema
from strategic_jobs import obj


def original(requested=(0,2)):
    return obj(dict(checks={'type':'array','items':obj({'claim_index':{'type':'integer','enum':list(requested)},
        'supported':{'type':'boolean'},'reason':{'type':['string','null']}})},
        quality=review_schema(['방법','데이터']),answer_supported={'type':'boolean'},
        missing_information={'type':'array','items':{'type':'string'}},
        citation_corrections={'type':'array','items':obj({'claim_index':{'type':'integer'},
            'evidence_ids':{'type':'array','items':{'type':'string','enum':['CITE1','CITE2']}}})}))


def wire():
    return dict(support=[2],reject=[dict(claim_index=0,reason='발췌에 없는 수치입니다.')],
        criteria_pass=[0,1,2,3],criteria_fail=[dict(criterion_index=4,reason='조건이 사실로 바뀌었습니다.')],
        addressed=[0],gaps=[dict(axis_index=1,reason='동일 조건 비교 자료는 없습니다.')],missing=[],
        answer_supported=False,missing_information=['동일 조건 비교'],
        citation_corrections=[dict(claim_index=2,evidence_ids=['CITE2'])])


def test_rejections_gaps_and_citation_corrections_keep_their_independent_meaning():
    value=wire();before=copy.deepcopy(value);decoded=restore(value,original())
    assert value==before
    assert decoded['checks']==[dict(claim_index=0,supported=False,reason='발췌에 없는 수치입니다.'),
                               dict(claim_index=2,supported=True,reason=None)]
    assert set(decoded['quality']['criteria'])==set(CRITERIA)
    assert decoded['quality']['axes'][1]['status']=='evidence_gap'
    assert decoded['citation_corrections']==value['citation_corrections']
    assert assess(decoded,['방법','데이터'])['status']=='needs_review'
    value.update(support=[0,2],reject=[],criteria_pass=list(range(5)),criteria_fail=[],answer_supported=True)
    assert assess(restore(value,original()),['방법','데이터'])['status']=='evidence_limited'
    value['missing']=value.pop('gaps');value['gaps']=[]
    assert assess(restore(value,original()),['방법','데이터'])['status']=='needs_review'


@pytest.mark.parametrize('mutate',[
    lambda v:v['support'].clear(),
    lambda v:v['support'].append(0),
    lambda v:v['support'].append(2),
    lambda v:v['support'].append(1),
    lambda v:v.update(support=[True]),
    lambda v:v['reject'][0].update(reason=' '),
    lambda v:v['criteria_pass'].pop(),
    lambda v:v['criteria_pass'].append(4),
    lambda v:v['criteria_fail'][0].update(criterion_index=9),
    lambda v:v['gaps'].clear(),
    lambda v:v['missing'].append(dict(axis_index=0,reason='중복')),
    lambda v:v['gaps'][0].update(axis_index=False),
    lambda v:v['gaps'][0].update(reason=None),
    lambda v:v.pop('citation_corrections'),
    lambda v:v.update(checks=[]),
])
def test_omitted_duplicate_unknown_and_mixed_decisions_never_become_approval(mutate):
    value=wire();mutate(value)
    with pytest.raises(RuntimeError):restore(value,original())


def test_empty_recheck_still_requires_every_quality_criterion_and_axis():
    value=wire();value.update(support=[],reject=[])
    assert restore(value,original(()))['checks']==[]
    assert schema(original(()))['properties']['support']['maxItems']==0
    value['criteria_pass'].pop()
    with pytest.raises(RuntimeError):restore(value,original(()))


def test_new_review_criteria_cannot_silently_disappear_from_wire_format():
    value=original();value['properties']['quality']['properties']['criteria']['properties']['new_criterion']={}
    with pytest.raises(RuntimeError):schema(value)


def test_grouped_review_rechecks_whole_explanation_even_when_claims_are_reused(monkeypatch):
    from test_answer_quality import execute,draft
    first=dict(support=[0],reject=[],criteria_pass=[0,1,3,4],
        criteria_fail=[dict(criterion_index=2,reason='차이를 설명해야 합니다.')],addressed=[0,1],
        gaps=[],missing=[],answer_supported=True,missing_information=[],citation_corrections=[])
    final=dict(first,support=[],criteria_pass=list(range(5)),criteria_fail=[])
    run,calls=execute(monkeypatch,[draft(),first,draft(),final])
    result=run()
    assert sufficient(result) and len(calls)==4
    assert result['verification']['reused_claim_checks']==1
    assert result['verification']['quality']['review_rounds'][0]['status']=='needs_review'
    assert result['verification']['quality']['review_rounds'][1]['status']=='passed'
