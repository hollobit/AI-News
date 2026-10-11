from copy import deepcopy

import pytest

from graph_answer_format import apply_patch, expand
from graph_answer_quality import assess
from graph_rag import validate_answer
from test_answer_quality import audit


def claim(text='같은 평가 조건에서만 비교할 수 있다.',kind='finding'):
    return dict(sentences=[text],kind=kind,axis='비교',evidence_ids=['e'],
                premises=None,assumptions=None,counterevidence=None,uncertainty=None)


def test_explanation_cards_and_citations_share_the_exact_authored_text():
    value=dict(claims=[claim(),claim('현장 성능을 확인한 자료는 미확보다.')],paragraphs=[[0],[1]],limitations=[1])
    original=deepcopy(value)
    result=expand(value)
    assert result['answer']=='같은 평가 조건에서만 비교할 수 있다. [e]\n\n현장 성능을 확인한 자료는 미확보다. [e]'
    assert result['limitations']==['현장 성능을 확인한 자료는 미확보다.']
    assert all('premises' not in c for c in result['claims'])
    assert validate_answer(result,[dict(id='e')])==result
    assert value==original


def test_inference_cannot_omit_conditions_while_fact_can():
    value=dict(claims=[claim(kind='insight')],paragraphs=[[0]],limitations=[])
    with pytest.raises(RuntimeError):validate_answer(expand(value),[dict(id='e')])


def test_inference_annotations_reference_verbatim_sentences_instead_of_regenerating():
    item=claim(kind='connection')
    item.update(sentences=['실험의 대상과 조건이 서로 다르다.','따라서 같은 조건을 맞추기 전에는 점수만으로 우열을 판단할 수 없다.'],
                premises=[0],assumptions=[1],counterevidence=[],uncertainty=[1])
    result=expand(dict(claims=[item],paragraphs=[[0]],limitations=[]))
    assert result['claims'][0]['premises']==item['sentences'][0]
    assert result['claims'][0]['uncertainty']==item['sentences'][1]
    assert result['claims'][0]['counterevidence']=='미확보'
    validate_answer(result,[dict(id='e')])
    item['uncertainty']=[2]
    with pytest.raises(RuntimeError,match='문장 연결'):expand(dict(claims=[item],paragraphs=[[0]],limitations=[]))


@pytest.mark.parametrize('layout',[[[0],[0]],[[1]],[],[[True]],[[]]])
def test_incomplete_or_duplicated_paragraph_mapping_is_rejected(layout):
    with pytest.raises(RuntimeError,match='문단'):
        expand(dict(claims=[claim()],paragraphs=layout,limitations=[]))


def test_patch_preserves_untouched_claims_and_rebases_only_after_deletion():
    original=dict(claims=[claim(),claim('거절된 문장'),claim('유지할 문장')],paragraphs=[[0,1],[2]],limitations=[2])
    before=deepcopy(original)
    patch=dict(changes=[dict(claim_index=1,replacement=None)],additions=[claim('새로 확보한 조건')],paragraphs=[[0],[2,3]],limitations=None)
    result,stats=apply_patch(original,patch,[1])
    assert result['claims']==[original['claims'][0],original['claims'][2],patch['additions'][0]]
    assert result['paragraphs']==[[0],[1,2]] and result['limitations']==[1]
    assert stats==dict(changed_claims=1,added_claims=1,retained_claims=2)
    assert original==before
    validate_answer(expand(result),[dict(id='e')])


@pytest.mark.parametrize('changes,layout',[
    ([],[[0],[1]]),
    ([dict(claim_index=1,replacement=None)],[[0],[1]]),
    ([dict(claim_index=1,replacement=claim()),dict(claim_index=1,replacement=claim())],[[0],[1]]),
    ([dict(claim_index=2,replacement=claim())],[[0],[1]]),
])
def test_patch_cannot_retain_rejected_claim_or_reference_deleted_claim(changes,layout):
    with pytest.raises(RuntimeError):
        apply_patch(dict(claims=[claim(),claim()],paragraphs=[[0],[1]],limitations=[]),
            dict(changes=changes,additions=[],paragraphs=layout,limitations=None),[1])


def test_compact_audit_still_requires_every_check_and_failure_explanation():
    value=audit()
    for check in value['checks']:check['reason']=None
    for check in value['quality']['criteria'].values():check['reason']=None
    for axis in value['quality']['axes']:axis['reason']=None
    assert assess(value,['方法','데이터'])['status']=='passed'
    value['quality']['criteria']['scope']['passed']=False
    with pytest.raises(RuntimeError,match='완결'):assess(value,['方法','데이터'])
    value['quality']['criteria']['scope']['reason']='원문에 없는 정의'
    assert assess(value,['方法','데이터'])['status']=='needs_review'


@pytest.mark.parametrize('limits',[['인용 없는 새로운 설명'],[1],[True],[0,0]])
def test_limitations_cannot_introduce_prose_outside_cited_claims(limits):
    with pytest.raises(RuntimeError):expand(dict(claims=[claim()],paragraphs=[[0]],limitations=limits))
