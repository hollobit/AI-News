"""Local corrections preserve approved explanations and still require a full review."""
import copy
import json

import pytest

from graph_answer_format import apply_patch, patch_schema
from graph_answer_quality import VERSION, local_repair_targets, sufficient
from graph_rag import GraphResult, answer_question
from test_answer_quality import audit
from test_graph_answer_format import claim


def verification():
    return dict(method='independent_evidence_review', claim_checks=[
        dict(claim_index=0, supported=True), dict(claim_index=1, supported=False)],
        quality=dict(policy=VERSION, status='needs_review',
            criteria={k: dict(passed=k != 'scope') for k in
                ('direct_answer', 'coverage', 'explanation', 'integration', 'scope')},
            axes=[dict(status='addressed'), dict(status='evidence_gap')]))


@pytest.mark.parametrize('criterion', ['direct_answer', 'coverage', 'explanation', 'integration'])
def test_explanatory_gaps_keep_the_broader_repair_path(criterion):
    review=verification()
    assert local_repair_targets(review)==[1]
    review['quality']['criteria'][criterion]['passed']=False
    assert local_repair_targets(review) is None


@pytest.mark.parametrize('change', [
    lambda v: v.update(method='citation_only'),
    lambda v: v['quality'].update(policy='old-policy'),
    lambda v: v['quality'].update(status='passed'),
    lambda v: v['quality'].update(axes=[]),
    lambda v: v['quality']['axes'][1].update(status='missing'),
    lambda v: v['quality']['criteria'].pop('scope'),
    lambda v: v.update(claim_checks=[]),
    lambda v: v['claim_checks'][1].update(supported=True),
])
def test_only_complete_independent_reviews_with_precise_rejections_can_be_local(change):
    review=verification();change(review)
    assert local_repair_targets(review) is None


def test_local_schema_disallows_new_claims_and_limits_repair_ids():
    original={'properties': {'claims': {'type': 'array', 'maxItems': 8,
                                        'items': {'type': 'object'}}}}
    schema=patch_schema(original, 4, targets=[1, 3])['properties']
    assert schema['changes']['minItems']==schema['changes']['maxItems']==2
    assert schema['changes']['items']['properties']['claim_index']['enum']==[1, 3]
    assert schema['additions']['maxItems']==0
    broad=patch_schema(original, 4)['properties']
    assert broad['additions']['maxItems']==8
    assert broad['changes']['items']['properties']['claim_index']['enum']==list(range(4))


@pytest.mark.parametrize('tamper', ['add', 'change_approved'])
def test_local_scope_is_enforced_even_if_provider_ignores_schema(tamper):
    original=dict(claims=[claim('승인된 설명'), claim('거절된 설명')], paragraphs=[[0],[1]], limitations=[])
    patch=dict(changes=[dict(claim_index=1, replacement=claim('교정된 설명'))],
               additions=[], paragraphs=[[0],[1]], limitations=None)
    if tamper=='add':
        patch['additions']=[claim('불필요한 확장')];patch['paragraphs'].append([2])
    else:patch['changes'].append(dict(claim_index=0, replacement=claim('승인된 설명을 바꿈')))
    with pytest.raises(RuntimeError, match='국소 오류'):
        apply_patch(original, patch, [1], targets=[1])


@pytest.mark.parametrize('extended', [True, False])
@pytest.mark.parametrize('final_passes', [True, False])
def test_local_repair_keeps_approved_claim_and_full_originals_in_final_review(monkeypatch, final_passes, extended):
    good=claim('시뮬레이션 평가 결과이다.')
    bad=claim('현실 환경의 평가가 전혀 없다.')
    fixed=claim('실제 창고 평가가 있으나 다른 현장 일반화는 미확인이다.')
    for c in (good,bad):c['evidence_ids']=['CITE1']
    fixed['evidence_ids']=['CITE2']
    first=audit();first['checks']=[dict(claim_index=0,supported=True,reason=None),
        dict(claim_index=1,supported=False,reason='인용하지 않은 자료에 실제 창고 평가가 있다.')]
    first['answer_supported']=False
    first['quality']['criteria']['scope']=dict(passed=False,reason='실제 창고 평가와 다른 현장 일반화를 구분해야 한다.')
    final=audit(supported=final_passes)
    final['checks']=[dict(claim_index=1,supported=final_passes,
        reason=None if final_passes else '교정한 설명도 근거 범위를 벗어난다.')]
    responses=[dict(claims=[good,bad],paragraphs=[[0],[1]],limitations=[]), first,
        dict(changes=[dict(claim_index=1,replacement=fixed)],additions=[],paragraphs=[[0],[1]],limitations=[1]), final]
    calls=[]
    def runner(prompt,schema,**kwargs):
        calls.append((prompt,schema,kwargs));return copy.deepcopy(responses.pop(0))
    monkeypatch.setattr('semantic.run_structured',runner)
    originals=[dict(id='a',text='시뮬레이션 평가 결과이다.'),
               dict(id='b',text='실제 창고에서 평가했다. 다른 현장 일반화는 확인되지 않았다.')]
    if extended:originals[0]['text']+=' 원문 조건.'*9000
    result=answer_question(GraphResult(),'벤치마크 연구 현황과 데이터세트 종류는?',detailed=True,
        retrieval=dict(nodes=[],edges=[],evidence=originals,no_hits=False),
        analysis_plan={'axes':['방법','데이터']})
    assert len(calls)==4 and sufficient(result)==final_passes
    assert [(c[2]['role'],c[2]['reasoning_effort']) for c in calls]==[
        ('graph_draft','high' if extended else 'medium'),
        ('strategic_question_verification','medium'),
        ('graph_answer','medium'),('strategic_question_verification','medium')]
    repair=json.loads(calls[2][0].split('\nDATA:\n',1)[1])
    recheck=json.loads(calls[3][0].split('\n',1)[1])
    assert [e['text'] for e in repair['evidence']]==[e['text'] for e in originals]
    assert recheck['evidence']==repair['evidence']
    assert recheck['claims']==[good,fixed]
    assert recheck['previously_supported_indices']==[0]
    assert recheck['review_claim_indices']==[1]
    assert calls[2][1]['properties']['additions']['maxItems']==0
    assert result['verification']['quality']['patch']==dict(
        changed_claims=1, added_claims=0, retained_claims=1, scope='rejected_claims_only')
    if not final_passes:
        assert result['verification']['quality']['status']=='needs_review'
        assert '완성하지 못했습니다' in result['answer']
