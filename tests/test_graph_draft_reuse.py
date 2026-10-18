"""A former answer is only an untrusted draft: current originals and fresh review required."""
from copy import deepcopy
import json
import sqlite3
import time
from unittest.mock import Mock

import pytest
from graph_answer_format import expand
from graph_draft_reuse import recover, select, SOURCE_FIELDS
from graph_answer_quality import sufficient
from system_one import decide as actual_decide
from graph_rag import answer_question
from test_graph_persistent_answer import context, run
from test_answer_quality import audit

QUESTION='최근 Physical AI 벤치마크의 평가 방법과 데이터세트 구성 차이, 성능 수치 해석의 주의점을 설명해주세요.'
PRIOR='Physical AI 벤치마크별 평가 방식과 데이터세트 구성은 어떻게 다르며 성능 수치를 어떻게 해석해야 하나요?'


def authored():
    return dict(claims=[
        dict(kind='finding',axis='방법',evidence_ids=['e'],passage={'explanation':'평가 조건이 다릅니다.'}),
        dict(kind='connection',axis='범위',evidence_ids=['e'],passage=dict(premises='두 실험의 조건이 다릅니다.',
             explanation='같은 조건에서 비교해야 합니다.',assumptions=None,counterevidence=None,uncertainty='현장 성능은 미확인입니다.'))],
        paragraphs=[[1],[0]],limitations=[1])


@pytest.mark.parametrize('optional',[None,'자료에서 확인되는 조건입니다.'])
def test_recovery_preserves_every_byte_and_field(optional):
    payload=authored()
    payload['claims'][1]['passage']['assumptions']=optional
    payload['claims'][1]['passage']['counterevidence']=optional
    core=expand(payload)
    restored=recover(core)
    assert restored==payload and expand(restored)==core
    restored['claims'][0]['passage']['explanation']='mutation'
    assert core==expand(payload)


@pytest.mark.parametrize('corrupt',['extra_prose','omitted_claim','duplicate','limits','fields','ambiguous','malformed'])
def test_unrecoverable_answers_fail_closed(corrupt):
    value=authored()
    if corrupt=='ambiguous':
        # A literal placeholder is ambiguous with the null sentinel; do not guess.
        value['claims'][1]['passage']['assumptions']='해당 없음'
    core=expand(value)
    if corrupt=='extra_prose':core['answer']+=' 추가 설명'
    elif corrupt=='omitted_claim':core['answer']=core['answer'].split('\n\n')[0]
    elif corrupt=='duplicate':core['answer']+='\n\n'+core['answer']
    elif corrupt=='limits':core['limitations']=['없는 문장']
    elif corrupt=='fields':core['claims'][1]['premises']='달라진 전제'
    elif corrupt=='malformed':core['claims']=[None]
    assert recover(core) is None


@pytest.fixture
def candidate_db(tmp_path,monkeypatch,context):
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODE','shadow')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODEL','jev-1.14.0')
    monkeypatch.setenv('NEWS_QUESTION_DRAFT_MODE','jev')
    monkeypatch.setenv('TYPESAFE_API_KEY','test-key-never-log')
    result=run(context)
    path=tmp_path/'news'
    request=dict(question=PRIOR,params={'workspace':['1']},node_ids=[])
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE graph_question_jobs(id,request_json,result_json,status,created_at)')
        db.execute('INSERT INTO graph_question_jobs VALUES(?,?,?,?,?)',('old',json.dumps(request),json.dumps(result),'complete',time.time()))
    decision=Mock(return_value=dict(status='complete',answers={'scope':dict(choice='equivalent',confidence=.98,
        probabilities=dict(equivalent=.98,different=.01,uncertain=.01))}))
    monkeypatch.setattr('system_one.decide',decision)
    return path,result,request,context[1]['evidence'],context[2],decision


def choose(fixture):
    path,_,_,evidence,plan,_=fixture
    return select(path,QUESTION,plan,evidence,job_id='new')


def test_selection_ignores_prior_verdict_and_uses_one_scope_decision(candidate_db):
    selected,metadata=choose(candidate_db)
    assert selected and metadata['selected'] and metadata['source_job_id']=='old'
    decision=candidate_db[-1];assert decision.call_count==1
    state=decision.call_args.args[2]
    assert set(state)=={'version','question','previous_question'}
    assert state['question']==QUESTION and state['previous_question']==PRIOR


@pytest.mark.parametrize('field',SOURCE_FIELDS)
def test_changed_cited_source_fields_block_selection(candidate_db,field):
    candidate_db[3][0][field]='changed'
    assert choose(candidate_db)[0] is None
    candidate_db[-1].assert_not_called()


@pytest.mark.parametrize('corrupt',['date','old_policy','method','review','status','age','future','filter','nodes','duplicate_id','malformed','missing_cite'])
def test_ineligible_records_never_request_a_decision(candidate_db,corrupt):
    path,result,request,evidence,plan,decision=candidate_db
    status='complete';created=time.time()
    if corrupt=='date':result['answer_as_of']='2000-01-01'
    elif corrupt=='old_policy':result['verification']['quality']['policy']='old'
    elif corrupt=='method':result['verification']['method']='self_review'
    elif corrupt=='review':result['verification']['quality']['status']='needs_review'
    elif corrupt=='status':status='stale'
    elif corrupt=='age':created-=86500
    elif corrupt=='future':created+=60
    elif corrupt=='filter':request['params']['date']=['2026-01-01']
    elif corrupt=='nodes':request['node_ids']=['node']
    elif corrupt=='duplicate_id':result['evidence']*=2
    elif corrupt=='malformed':result=[]
    elif corrupt=='missing_cite':evidence.clear()
    with sqlite3.connect(path) as db:
        db.execute('UPDATE graph_question_jobs SET request_json=?,result_json=?,status=?,created_at=?',
            (json.dumps(request),json.dumps(result),status,created))
    assert choose(candidate_db)[0] is None
    decision.assert_not_called()


@pytest.mark.parametrize('outcome',['uncertain','different','low_confidence','low_probability','busy'])
def test_uncertain_decision_does_not_retry_other_drafts(candidate_db,outcome):
    decision=candidate_db[-1];result=decision.return_value;answer=result['answers']['scope']
    if outcome in ('uncertain','different'):answer['choice']=outcome
    elif outcome=='low_confidence':answer['confidence']=.94
    elif outcome=='low_probability':answer['probabilities']['equivalent']=.94
    else:result['status']='busy'
    selected,metadata=choose(candidate_db)
    assert selected is None and not metadata['selected'] and decision.call_count==1


@pytest.mark.parametrize('setting,value',[('NEWS_QUESTION_DRAFT_MODE','off'),('NEWS_SYSTEM_ONE_MODE','off'),('NEWS_SYSTEM_ONE_MODEL','unvalidated')])
def test_disabled_and_unvalidated_routes_have_no_calls(candidate_db,monkeypatch,setting,value):
    monkeypatch.setenv(setting,value)
    assert choose(candidate_db)[0] is None
    candidate_db[-1].assert_not_called()


def test_fresh_review_includes_all_current_uncited_originals_and_no_prior_checks(context,monkeypatch):
    old=run(context)
    payload=recover({k:old[k] for k in ('answer','claims','limitations')})
    graph,bundle,plan,calls=context
    bundle['evidence'].append(dict(id='counter',title='반대 결과',text='다른 조건에서는 성공률 30%다.',source_url='https://example.org/b'))
    captured=[]
    def reviewer(prompt,schema,**kwargs):
        assert kwargs['role']=='strategic_question_verification'
        data=json.loads(prompt.split('\n',1)[1]);captured.append(data)
        assert data['review_claim_indices']==[0] and data['previously_supported_indices']==[]
        assert {e['text'] for e in data['evidence']}=={e['text'] for e in bundle['evidence']}
        return audit(axes=('addressed',))
    monkeypatch.setattr('semantic.run_structured',reviewer)
    events=[]
    lookup=Mock(return_value=(payload,dict(selected=True,source_job_id='old')))
    result=answer_question(graph,'같은 실험 조건의 결과를 설명해주세요.',retrieval=bundle,analysis_plan=plan,detailed=True,
        draft_lookup=lookup,progress=lambda stage,**kw:events.append((stage,kw)))
    assert sufficient(result) and len(captured)==1 and result['verification']['reused_claim_checks']==0
    assert [s['stage'] for s in result['timing']['stages']]==['draft_reuse','review']
    assert result['timing']['answer_cache_source']=='reviewed_draft' and not result['timing']['answer_cache_hit']
    assert result['verification']['draft_reuse']['review_status']=='passed'
    assert ('synthesis',{'phase':'candidate'}) in events and ('synthesis',{'phase':'reuse'}) in events
    lookup.reset_mock()
    again=answer_question(graph,'같은 실험 조건의 결과를 설명해주세요.',retrieval=bundle,analysis_plan=plan,detailed=True,draft_lookup=lookup)
    assert again['timing']['answer_cache_hit'];lookup.assert_not_called()
    assert len(captured)==1


@pytest.mark.parametrize('passes',[True,False])
def test_rejected_candidate_gets_one_repair_and_recheck_never_old_approval(context,monkeypatch,passes):
    old=run(context);payload=recover({k:old[k] for k in ('answer','claims','limitations')})
    seen=[]
    def model(prompt,schema,**kwargs):
        seen.append(kwargs['role'])
        if 'changes' in schema['properties']:
            changed=deepcopy(payload['claims'][0]);changed['passage']['explanation']='원문에서 실험 성공률 90%라고 설명합니다.'
            return dict(changes=[dict(claim_index=0,replacement=changed)],additions=[],paragraphs=[[0]],limitations=None)
        return audit(supported=passes and len(seen)==3,axes=('addressed',))
    monkeypatch.setattr('semantic.run_structured',model)
    graph,bundle,plan,_=context
    result=answer_question(graph,'평가된 실험의 결과와 한계는?',retrieval=bundle,analysis_plan=plan,detailed=True,
        draft_lookup=lambda:(payload,dict(selected=True,source_job_id='old')))
    assert seen==['strategic_question_verification','graph_answer','strategic_question_verification']
    assert sufficient(result)==passes
    assert result['verification']['quality']['revision_count']==1
    assert [r['status'] for r in result['verification']['quality']['review_rounds']][0]=='needs_review'
    assert result['verification']['draft_reuse']['revised']
    if not passes:assert '완성하지 못했습니다' in result['answer']


def test_draft_decision_reads_exact_cache_during_shadow_work(candidate_db,monkeypatch):
    import system_one
    path=candidate_db[0]
    cached=dict(status='complete',cached=True,answers={})
    evaluate=Mock(return_value=cached)
    monkeypatch.setattr(system_one,'evaluate',evaluate)
    assert system_one.PENDING.acquire(blocking=False)
    try:
        assert actual_decide(path,'question_draft',{}, {}) is cached
        evaluate.assert_called_once()
    finally:system_one.PENDING.release()


def test_synthesis_reuse_progress_does_not_inherit_full_writing_eta(monkeypatch):
    from question_progress import setup,update,view,STAGES
    db=sqlite3.connect(':memory:');setup(db)
    db.execute('CREATE TABLE graph_question_jobs(id,status)')
    db.executemany('INSERT INTO graph_question_jobs VALUES(?,?)',[('old','complete'),('new','running')])
    for stage in STAGES:
        monkeypatch.setattr('question_progress.time.time',lambda:100)
        update(db,'old',stage)
        monkeypatch.setattr('question_progress.time.time',lambda:160)
        update(db,'old',stage,done=True)
    monkeypatch.setattr('question_progress.time.time',lambda:200)
    for stage in ('evidence','plan'):update(db,'new',stage,done=True)
    update(db,'new','synthesis',phase='candidate')
    progress=view(db,'new','running',200)
    assert progress['remaining_seconds'] is None
    assert progress['stages'][2]['label']=='기존 설명·현재 원자료 일치 확인'
    update(db,'new','synthesis',phase='reuse')
    assert view(db,'new','running',200)['stages'][2]['label']=='기존 설명 복원 · 새 질문의 검토 준비'


def test_draft_provider_contention_is_bounded_without_network_retries(candidate_db,monkeypatch):
    import system_one
    from test_decision_plan_admission import provider_lock
    path=candidate_db[0]
    transport=Mock(side_effect=AssertionError('busy provider must not be called'))
    monkeypatch.setattr(system_one,'transport',transport)
    with provider_lock(path):
        start=time.monotonic()
        result=actual_decide(path,'question_draft',{'question':QUESTION},{'scope':{'irrelevant':'lock precedes validation'}})
        assert result['status']=='busy' and .2<=time.monotonic()-start<1
    transport.assert_not_called()


def add_candidate(fixture, identity, question, age=1, result=None):
    path, saved, request, *_ = fixture
    request=dict(request,question=question)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO graph_question_jobs VALUES(?,?,?,?,?)',
            (identity,json.dumps(request),json.dumps(saved if result is None else result),'complete',time.time()-age))


def scope_result(choice='equivalent',confidence=.98):
    return dict(choice=choice,confidence=confidence,
        probabilities={name:confidence if name==choice else (1-confidence)/2
                       for name in ('equivalent','different','uncertain')})


def test_older_identical_question_is_not_hidden_by_newer_different_task(candidate_db):
    add_candidate(candidate_db,'exact',QUESTION)
    selected,metadata=choose(candidate_db)
    assert selected and metadata['source_job_id']=='exact'
    assert metadata['selection_method']=='exact_question'
    candidate_db[-1].assert_not_called()


def test_identical_question_still_needs_current_cited_sources(candidate_db):
    saved=deepcopy(candidate_db[1]);saved['evidence'][0]['text']='과거 입력'
    add_candidate(candidate_db,'changed',QUESTION,result=saved)
    candidate_db[-1].return_value['answers']['scope']=scope_result('different')
    selected,metadata=choose(candidate_db)
    assert selected is None and not metadata['selected']
    assert metadata['source_job_id']=='old' and candidate_db[-1].call_count==1


def test_one_batch_can_select_older_equivalent_question_after_different_task(candidate_db):
    question='최근 '+PRIOR
    add_candidate(candidate_db,'older',question)
    add_candidate(candidate_db,'duplicate',question,age=2)
    decision=candidate_db[-1]
    decision.return_value=dict(status='complete',answers={
        'scope_0':scope_result('different'),'scope_1':scope_result()})
    selected,metadata=choose(candidate_db)
    assert selected and metadata['source_job_id']=='older'
    assert decision.call_count==1 and metadata['candidate_questions']==2
    state=decision.call_args.args[2];questions=decision.call_args.args[3]
    assert state['previous_questions']==[PRIOR,question]
    assert len(questions)==2
    assert 'state.previous_questions[1]' in questions['scope_1']['instructions']


@pytest.mark.parametrize('outcome',['different','uncertain','low_confidence','incomplete','busy'])
def test_multi_candidate_decision_never_retries_or_trusts_partial_results(candidate_db,outcome):
    add_candidate(candidate_db,'older','최근 '+PRIOR)
    decision=candidate_db[-1]
    answers={'scope_0':scope_result('different'),'scope_1':scope_result(outcome if outcome in ('different','uncertain') else 'equivalent')}
    if outcome=='low_confidence':answers['scope_1']['confidence']=.94
    if outcome=='incomplete':answers.pop('scope_0')
    decision.return_value=dict(status='busy' if outcome=='busy' else 'complete',answers=answers)
    assert choose(candidate_db)[0] is None
    assert decision.call_count==1
    assert len(decision.call_args.args[3])==2


def test_exact_question_lookup_precedes_semantic_candidate_bound(candidate_db):
    from graph_draft_reuse import MAX_SCOPE_CANDIDATES
    for i in range(MAX_SCOPE_CANDIDATES+2):
        add_candidate(candidate_db,'q'+str(i),chr(65+i)+' '+PRIOR,age=i+1)
    add_candidate(candidate_db,'exact',QUESTION,age=20)
    selected,metadata=choose(candidate_db)
    assert selected and metadata['source_job_id']=='exact'
    candidate_db[-1].assert_not_called()
    with sqlite3.connect(candidate_db[0]) as db:db.execute("DELETE FROM graph_question_jobs WHERE id='exact'")
    candidate_db[-1].return_value=dict(status='busy')
    assert choose(candidate_db)[0] is None
    assert len(candidate_db[-1].call_args.args[2]['previous_questions'])==MAX_SCOPE_CANDIDATES
    assert candidate_db[-1].call_count==1
