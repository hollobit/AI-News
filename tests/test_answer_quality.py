"""Bounded explanation repair must never bypass grounding or current-source gates."""
import copy
import json
import time
from collections import OrderedDict

import pytest

from graph_answer_quality import VERSION, assess, sufficient
from graph_rag import GraphResult, answer_question


def wire_claim(claim):
    value=copy.deepcopy(claim)
    value['sentences']=[value.pop('text')]
    for field in ('premises','assumptions','counterevidence','uncertainty'):
        value[field]=None if value['kind']=='finding' else [] if field in ('assumptions','counterevidence') else [0]
    return value


@pytest.fixture(autouse=True)
def isolated_answer_cache(monkeypatch):
    monkeypatch.setattr('graph_answer_cache._CACHE',OrderedDict())


def audit(*, explanation=True, supported=True, axes=('addressed', 'addressed')):
    return dict(checks=[dict(claim_index=0, supported=supported, reason='원문 조건 대조')],
        answer_supported=supported, missing_information=[], quality=dict(
            criteria={key: dict(passed=explanation if key == 'explanation' else True,
                reason='이름 나열 대신 방법과 데이터의 차이가 평가에 미치는 의미를 설명하세요.' if key == 'explanation' and not explanation else '질문의 요구와 원문 범위를 충족합니다.')
                for key in ('direct_answer', 'coverage', 'explanation', 'integration', 'scope')},
            axes=[dict(axis_index=i, status=status, reason='자료 범위를 한정하여 설명했습니다.' if status != 'missing' else '데이터 종류 설명 누락') for i, status in enumerate(axes)]))


def draft(text='평가 방법과 데이터의 범위가 달라 비교 조건을 맞춰야 합니다.\n\n같은 조건의 평가 근거는 미확보입니다.'):
    return dict(answer=text, claims=[dict(text='평가 방법과 데이터의 범위가 다릅니다.', evidence_ids=['source:e'],
        axis='비교 조건', kind='connection', premises='제공된 서로 다른 평가 조건', assumptions='동일 조건의 비교 필요',
        counterevidence='미확보', uncertainty='공통 조건 실험 미확보')], limitations=[])


def execute(monkeypatch, responses, *, graph=None, events=None):
    calls=[]
    def runner(prompt, schema, **kwargs):
        calls.append((prompt, schema))
        value=responses.pop(0)
        if isinstance(value, Exception): raise value
        if 'checks' in value:
            value=copy.deepcopy(value)
            requested=json.loads(prompt.split('\n',1)[1])['review_claim_indices']
            value['checks']=[c for c in value['checks'] if c['claim_index'] in requested]
        if 'answer' in value and 'paragraphs' in schema['properties']:
            value=copy.deepcopy(value)
            value['claims'][0]['text']=value['answer']
            value['claims']=[wire_claim(c) for c in value['claims']]
            if 'changes' in schema['properties']:
                value=dict(changes=[dict(claim_index=0,replacement=value['claims'][0])],additions=[],
                           paragraphs=[[0]],limitations=value['limitations'])
            else:value=dict(claims=value['claims'],paragraphs=[[0]],limitations=value['limitations'])
        return copy.deepcopy(value)
    monkeypatch.setattr('semantic.run_structured', runner)
    evidence=[dict(id='source:e', title='평가 조건', text='평가 방법과 데이터의 범위가 다르다.', source_url='https://example.org/e')]
    retrieval=dict(nodes=[], edges=[], evidence=evidence, no_hits=False, limitations=[])
    graph=graph if graph is not None else GraphResult()
    def run(**kwargs):
        return answer_question(graph, '평가 방법과 데이터 차이는?', retrieval=retrieval,
            analysis_plan={'axes': ['방법', '데이터']}, detailed=True,
            progress=(lambda stage, **detail: events.append((stage, detail))) if events is not None else None, **kwargs)
    return run, calls


def test_good_explanation_uses_existing_two_calls_and_remains_reusable(monkeypatch):
    graph=GraphResult();graph.search_key='adequate-answer'
    run,calls=execute(monkeypatch,[draft(),audit()],graph=graph)
    first=run();cached=run();rechecked=run(reuse_only=first)
    assert sufficient(first) and len(calls)==2
    assert cached['timing']['answer_cache_hit'] and rechecked['timing']['answer_cache_hit']
    assert first['verification']['quality']['revision_count']==0
    assert first['answer'].count('\n\n')==1


def test_list_only_answer_is_repaired_and_independently_rechecked(monkeypatch):
    events=[]
    run,calls=execute(monkeypatch,[draft('방법 A. 데이터 B.'),audit(explanation=False),draft(),audit()],events=events)
    result=run()
    assert sufficient(result) and len(calls)==4
    quality=result['verification']['quality']
    assert quality['revision_count']==1
    assert [r['status'] for r in quality['review_rounds']]==['needs_review','passed']
    assert '차이가 평가에 미치는 의미' in calls[2][0]
    assert 'checks' in calls[3][1]['properties'] and result['answer'] in json.loads(calls[3][0].split('\n',1)[1])['answer']
    assert ('review', {'phase':'repair'}) in events and ('review', {'phase':'recheck'}) in events
    assert events[-1][0]=='finalize'


def test_grounding_failure_triggers_repair_even_if_explanation_criteria_pass(monkeypatch):
    run,calls=execute(monkeypatch,[draft('검증되지 않은 주장'),audit(supported=False),draft(),audit()])
    result=run()
    assert sufficient(result) and len(calls)==4
    assert result['verification']['quality']['review_rounds'][0]['removed_unsupported_claims']==1
    assert result['claims'][0]['evidence_ids']==['source:e']


def test_new_definition_in_prose_cannot_pass_even_when_all_claims_are_supported(monkeypatch):
    ungrounded=audit();ungrounded['answer_supported']=False
    ungrounded['quality']['criteria']['scope']=dict(passed=False,reason='원문에 없는 용어 정의를 삭제해야 합니다.')
    run,calls=execute(monkeypatch,[draft(),audit(explanation=False),draft('few-shot의 미확인 정의'),ungrounded])
    result=run()
    assert not sufficient(result) and result['verification']['removed_unsupported_claims']==0
    assert 'few-shot' not in result['answer']
    assert '용어 정의' in result['verification']['quality']['issues'][0]


def test_repair_budget_exhausted_keeps_partial_separate_and_is_not_cached(monkeypatch):
    graph=GraphResult();graph.search_key='inadequate-answer'
    responses=[draft('단순 나열'),audit(explanation=False)]*4
    events=[]
    run,calls=execute(monkeypatch,responses,graph=graph,events=events)
    first=run();second=run()
    assert len(calls)==8 and not sufficient(first) and not sufficient(second)
    assert first['verification']['quality']['revision_count']==1
    assert '완성하지 못했습니다' in first['answer'] and '단순 나열' not in first['answer']
    assert first['claims'] and not any(stage=='finalize' for stage,_ in events)
    from graph_answer_cache import AnswerInputsChanged
    with pytest.raises(AnswerInputsChanged):run(reuse_only=first)
    assert len(calls)==8


def test_real_evidence_gap_can_pass_but_omitted_axis_cannot():
    limited=assess(audit(axes=('addressed','evidence_gap')),['방법','데이터'])
    assert limited['status']=='evidence_limited'
    missing=assess(audit(axes=('addressed','missing')),['방법','데이터'])
    assert missing['status']=='needs_review' and '데이터 종류 설명 누락' in missing['issues']


@pytest.mark.parametrize('mutation',[
    lambda a:a.pop('quality'),
    lambda a:a['quality']['criteria'].pop('coverage'),
    lambda a:a['quality']['axes'].pop(),
    lambda a:a['quality']['axes'][1].update(axis_index=0),
    lambda a:a['quality']['criteria']['scope'].update(passed='yes'),
    lambda a:a['quality']['axes'][0].update(status='passed'),
])
def test_incomplete_reviewer_response_fails_closed(mutation):
    value=audit();mutation(value)
    with pytest.raises(RuntimeError,match='검토가 완결되지'):assess(value,['방법','데이터'])


def test_invalid_repair_citations_are_not_reviewed_or_published(monkeypatch):
    broken=draft();broken['claims'][0]['evidence_ids']=['invented']
    run,calls=execute(monkeypatch,[draft(),audit(explanation=False),broken])
    with pytest.raises(RuntimeError,match='연결되지 않은'):run()
    assert len(calls)==3


def test_failed_second_review_does_not_return_unreviewed_repair(monkeypatch):
    run,calls=execute(monkeypatch,[draft(),audit(explanation=False),draft('새 보완 설명'),RuntimeError('검토 시간 초과')])
    with pytest.raises(RuntimeError,match='검토 시간 초과'):run()
    assert len(calls)==4


def test_repair_keeps_full_uncited_context_and_original_claim_ids(monkeypatch):
    run,calls=execute(monkeypatch,[draft(),audit(explanation=False),draft(),audit()])
    result=run()
    payload=json.loads(calls[2][0].split('\nDATA:\n')[1])
    assert payload['evidence'][0]['text']=='평가 방법과 데이터의 범위가 다르다.'
    assert payload['draft']['claims'][0]['evidence_ids']==[payload['evidence'][0]['id']]
    assert result['claims'][0]['evidence_ids']==['source:e']


def test_patch_only_adds_missing_explanation_and_full_merged_answer_is_reviewed(monkeypatch):
    first=draft('평가 조건이 다르므로 점수를 바로 비교할 수 없습니다.')
    missing=audit(axes=('addressed','missing'))
    added=copy.deepcopy(first['claims'][0]);added['text']='데이터 범위의 차이 때문에 적용 가능성을 별도로 판단해야 합니다.'
    patch=dict(changes=[],additions=[wire_claim(added)],paragraphs=[[0],[1]],limitations=None)
    final=audit();final['checks'].append(dict(claim_index=1,supported=True,reason=None))
    run,calls=execute(monkeypatch,[first,missing,patch,final])
    result=run()
    assert sufficient(result) and len(calls)==4
    assert result['claims'][0]['text']==first['answer']
    checked=json.loads(calls[3][0].split('\n',1)[1])
    assert len(checked['claims'])==2 and result['answer'] in checked['answer']
    assert result['verification']['quality']['patch']==dict(changed_claims=0,added_claims=1,retained_claims=1)
    assert checked['review_claim_indices']==[1] and checked['previously_supported_indices']==[0]
    assert result['verification']['reused_claim_checks']==1
    assert [s['stage'] for s in result['timing']['stages']]==['generation','review','repair','recheck']


def test_claim_rejection_with_no_reason_cannot_pass_as_compact_audit(monkeypatch):
    rejected=audit(supported=False);rejected['checks'][0]['reason']=None
    run,calls=execute(monkeypatch,[draft(),rejected])
    with pytest.raises(RuntimeError,match='검토가 완결되지'):run()
    assert len(calls)==2


def test_review_reuse_requires_exact_full_context_and_never_skips_explanation(monkeypatch):
    from graph_rag import review_answer
    from graph_answer_quality import PROFILE
    data=dict(question='평가 방법과 데이터 차이는?',evidence=[dict(id='source:e',text='원문 조건')],
              analysis_plan=dict(axes=['방법','데이터']),answer_profile=PROFILE)
    requests=[];reject_explanation=False
    def runner(prompt,schema,**kwargs):
        payload=json.loads(prompt.split('\n',1)[1]);requests.append(payload)
        value=audit()
        value['checks']=[c for c in value['checks'] if c['claim_index'] in payload['review_claim_indices']]
        value['answer_supported']=not reject_explanation
        return value
    monkeypatch.setattr('semantic.run_structured',runner)
    candidate=draft();first=review_answer(copy.deepcopy(candidate),data)
    previous=dict(candidate=candidate,verification=first['verification'])
    second=review_answer(copy.deepcopy(candidate),data,previous=previous)
    assert requests[-1]['review_claim_indices']==[] and second['verification']['reused_claim_checks']==1
    reject_explanation=True
    invalid=review_answer(dict(candidate,answer='근거 없는 새 정의'),data,previous=previous)
    assert not sufficient(invalid) and invalid['verification']['reused_claim_checks']==1
    assert requests[-1]['answer']=='근거 없는 새 정의'
    reject_explanation=False
    data['evidence'][0]['text']+=' 바뀐 제한 조건'
    third=review_answer(copy.deepcopy(candidate),data,previous=previous)
    assert requests[-1]['review_claim_indices']==[0] and third['verification']['reused_claim_checks']==0


def test_successful_review_does_not_append_new_uncited_reviewer_prose(monkeypatch):
    value=audit();value['missing_information']=['검토자가 추가로 작성한 별도 사실 또는 작업 지시']
    run,_=execute(monkeypatch,[draft(),value])
    result=run()
    assert sufficient(result) and result['limitations']==[]


@pytest.mark.parametrize('no_hits',[False,True])
def test_insufficient_job_is_terminal_and_legacy_completion_is_stale(tmp_path,monkeypatch,no_hits):
    from graph_questions import GraphQuestions
    class Snapshots:
        def request(self,params):return 'rev',object(),None
        def key(self,params):return 'rev'
        def close(self):pass
    result=draft();result['verification']=dict(method='independent_evidence_review',quality=dict(policy=VERSION,status='needs_review'))
    if no_hits:result=dict(answer='질문에 답할 근거가 없습니다.',claims=[],evidence=[],limitations=[])
    monkeypatch.setattr('graph_plans.prepare',lambda *a:({'queries':['방법'],'axes':['방법','데이터']},False))
    monkeypatch.setattr('graph_questions.quick_answer',lambda *a:{'evidence':[{'id':'source:e'}]})
    def analyze(*args,progress=None,**kw):
        progress('synthesis');progress('review',phase='repair');return copy.deepcopy(result)
    monkeypatch.setattr('graph_questions.deepen',analyze)
    service=GraphQuestions(tmp_path/'questions.db',snapshots=Snapshots())
    try:
        response=service.ask('방법과 데이터 차이는?',params={'workspace':['1']})
        deadline=time.monotonic()+3
        while service.active and time.monotonic()<deadline:time.sleep(.01)
        identity=response['job']['id'];job=service.get(identity)
        assert job['status']==('evidence_only' if no_hits else 'needs_review') and job['progress']['percent']<100
        assert not sufficient(job['result'])
        with service.db() as db:
            db.execute("UPDATE graph_question_jobs SET status='complete',result_json=? WHERE id=?",(json.dumps(draft()),identity))
        legacy=service.get(identity)
        assert legacy['status']=='stale' and not legacy['result']
    finally:service.close()
