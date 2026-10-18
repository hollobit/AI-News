import copy
import json
import sqlite3
import threading
import urllib.error
from unittest.mock import Mock

import pytest

import system_one
from decision_tasks import retrieval
from graph_plan_router import candidate
from graph_plans import prepare, valid

TREND='최근 Physical AI 관련 뉴스와 논문에서 공통으로 나타나는 변화와 쟁점은 무엇인가요?'
PRIORITY='에이전트 환경에서의 개인정보 보호, 보안 이슈 중 가장 심각하게 다루어지고 있는 이슈는 ?'
COMPARISON='최근 Physical AI 벤치마크의 평가 방법과 데이터세트 구성 차이를 설명하고, 보고된 성능 수치를 해석할 때 주의할 점을 정리해주세요.'
FALLBACK={'queries':['맞춤 검색'], 'axes':['요청별 검토']}


def response(body, *, intent=None, coverage='complete', confidence=.99):
    data=json.loads(body)
    selected=intent or candidate(data['state']['question'])[0]
    answers={}
    for key,q in data['questions'].items():
        label=selected if key=='intent' else coverage
        answers[key]=dict(type='choice',choice=label,confidence=confidence,
            probabilities={k:1.0 if k==label else 0.0 for k in q['criteria']})
    return dict(model=data['model'],answers=answers)


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODE','shadow')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODEL','jev-1.14.0')
    monkeypatch.setenv('NEWS_QUESTION_PLAN_MODE','jev')
    monkeypatch.setenv('TYPESAFE_API_KEY','test-key-not-real')
    caller=Mock(side_effect=response)
    monkeypatch.setattr(system_one,'transport',caller)
    return caller


@pytest.mark.parametrize('question,intent',[(TREND,'trend'),(PRIORITY,'priority'),
    ('최근 Physical AI 관련 벤치마크 방법 연구 현황과 데이터세트 종류, 이슈는 ?', 'research'),
    ('PhysAI-Bench의 연구 현황에서 평가 방법과 데이터세트 구성, 성능 해석의 한계는 무엇인가요?', 'research'),
    ('Physical AI Smart Spaces의 연구 현황에서 평가 방법과 데이터세트 구성, 해석의 한계는 무엇인가요?', 'research'),
    ('최근 Physical AI 벤치마크 연구 현황에서 주요 평가 방법과 데이터세트 유형, 비교할 때의 한계를 설명해주세요.', 'research'),
    (COMPARISON,'benchmark_comparison'),
    ('Physical AI 벤치마크별 평가 방식과 데이터세트 구성은 어떻게 다르며 성능 수치를 어떻게 해석해야 하나요?', 'benchmark_comparison')])
def test_accepted_template_preserves_question_skips_sol_and_reuses_exact_plan(tmp_path,monkeypatch,runtime,question,intent):
    sol=Mock(side_effect=AssertionError('No Sol planning for accepted template'))
    monkeypatch.setattr('semantic.run_structured',sol)
    path=tmp_path/'news.db';updates=[]
    plan,cached=prepare(question,path,job_id='q',report=updates.append)
    assert not cached and valid(plan)
    assert plan['queries'][0]==question and plan['axes'][0]==question
    assert updates[-1]['method']=='jev_template' and updates[-1]['template']==intent
    assert not any('routing' in p for p in plan)
    assert prepare(question,path,report=updates.append)==(plan,True)
    assert updates[-1]['method']=='cache' and runtime.call_count==1
    with sqlite3.connect(system_one.db_path(path)) as db:
        identity,result=db.execute('select subject_id,result_json from decision_observations').fetchone()
    assert identity=='q' and json.loads(result)['application']['applied']


@pytest.mark.parametrize('override',[{'confidence':.89},{'coverage':'custom'},{'intent':'other'},{'intent':'priority'}])
def test_uncertain_or_mismatched_choice_runs_existing_sol_once(tmp_path,monkeypatch,runtime,override):
    runtime.side_effect=lambda body:response(body,**override)
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    updates=[]
    plan,cached=prepare(TREND,tmp_path/'news',report=updates.append)
    assert plan==FALLBACK and not cached and sol.call_count==1 and runtime.call_count==1
    assert updates[-1]['reason']=='uncertain'
    with sqlite3.connect(system_one.db_path(tmp_path/'news')) as db:
        result=json.loads(db.execute('select result_json from decision_observations').fetchone()[0])
    assert result['application']==dict(scope='question_plan',applied=False,reason='uncertain')


@pytest.mark.parametrize('question',[
    'Physical AI 벤치마크와 데이터세트 차이는?',
    '2026년 이후 로봇 뉴스의 공통 변화와 쟁점은?',
    '중국과 미국을 비교해 공통 변화와 쟁점을 설명해 주세요',
    '최근 AI 공통 변화와 쟁점은? 의료에서는?',
    '개인정보 위험 우선순위와 해결 전략은?',
    '공통 변화와 쟁점을 보안 이슈 우선순위와 함께 알려줘',
    '만약 규제가 달라지면 가장 심각한 이슈는?',
    'Physical AI 공통 변화와 쟁점에서 한국을 제외해 줘',
    '의료 AI에서 가장 중요한 위험과 치료 추천은?',
    '최근 뉴스의 공통 변화와 쟁점\n조건별 분석도 해줘',
    'AI 벤치마크 연구 현황과 데이터세트 유형 및 모델별 차이, 비교할 때의 한계는?',
    'AI 벤치마크 연구 현황과 데이터세트 유형을 각각 비교하고 비교할 때의 한계를 설명해줘',
    '2026년 AI 벤치마크 연구 현황과 데이터세트 유형, 비교할 때의 한계는?',
    'AI 벤치마크 연구 현황과 데이터세트 유형, 특정 조건에서 비교할 때의 한계는?',
    'AI 벤치마크 연구 현황과 데이터세트 유형, 한국을 제외하고 비교할 때의 한계는?',
    'AI 뉴스의 공통 변화와 쟁점에서 비교할 때의 한계는?',
    'PhysAI-Bench의 연구 현황에서 평가 방법과 데이터세트 구성, 해석의 한계와 치료 결정은?',
    'PhysAI-Bench의 연구 현황에서 평가 방법과 데이터세트 구성, 해석의 한계와 구매 판단은?',
    'PhysAI-Bench의 연구 현황에서 평가 방법과 데이터세트 구성, 해석의 한계와 OtherBench의 결과는?',
])
def test_custom_questions_skip_jev_and_preserve_full_question_for_sol(tmp_path,monkeypatch,runtime,question):
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    assert candidate(question) is None
    assert prepare(question,tmp_path/'news')[0]==FALLBACK
    assert question in sol.call_args.args[0]
    runtime.assert_not_called()


def test_comparison_limit_template_still_requires_jev_scope_approval(tmp_path,monkeypatch,runtime):
    question='AI 벤치마크 연구 현황과 데이터세트 유형, 비교할 때의 한계는?'
    runtime.side_effect=lambda body:response(body,coverage='custom')
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    updates=[]
    assert prepare(question,tmp_path/'news',report=updates.append)[0]==FALLBACK
    assert updates[-1]['reason']=='uncertain'
    sent=json.loads(runtime.call_args.args[0])['state']
    assert sent['question']==question
    assert sent['proposed_plan']['queries'][0]==question and sent['proposed_plan']['axes'][0]==question
    assert question in sol.call_args.args[0]


def test_benchmark_comparison_preserves_all_axes_and_sol_draft(tmp_path,monkeypatch):
    from model_policy import question_draft_role
    monkeypatch.setattr('semantic.run_structured',Mock(side_effect=AssertionError('No planning call')))
    plan,_=prepare(COMPARISON,tmp_path/'news')
    assert len(plan['axes'])==4 and plan['axes'][0]==COMPARISON
    assert all(COMPARISON.rstrip('.?') in q for q in plan['queries'])
    assert question_draft_role(COMPARISON,plan,evidence=[{'text':'원자료'}])=='graph_answer'


@pytest.mark.parametrize('question',[
    'PhysAI-Bench와 Smart Spaces 벤치마크의 평가 방법과 데이터세트 구성 차이, 성능 수치 해석은?',
    'Physical AI 벤치마크 평가 방법과 데이터세트 구성 차이, 성능 해석과 치료 결정은?',
])
def test_specific_alternatives_or_added_requirements_need_independent_scope_approval(tmp_path,monkeypatch,runtime,question):
    runtime.side_effect=lambda body:response(body,coverage='custom')
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    assert candidate(question)[0]=='benchmark_comparison'
    assert prepare(question,tmp_path/'news')[0]==FALLBACK
    assert question in sol.call_args.args[0] and sol.call_count==1


@pytest.mark.parametrize('extra',['2026년 이후 ', '한국을 제외하고 ', '각각 ', '특정 조건에서 ', '성과를 예측하고 ', '모델별로 '])
def test_benchmark_template_does_not_drop_filters_or_extra_scope(tmp_path,monkeypatch,runtime,extra):
    question=extra+COMPARISON
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    assert candidate(question) is None
    assert prepare(question,tmp_path/'news')[0]==FALLBACK
    assert question in sol.call_args.args[0]
    runtime.assert_not_called()


@pytest.mark.parametrize('env,value',[('NEWS_QUESTION_PLAN_MODE','off'),('NEWS_QUESTION_PLAN_MODE','invalid'),
    ('NEWS_SYSTEM_ONE_MODE','off'),('NEWS_SYSTEM_ONE_MODEL','jev-1.13.0'),('TYPESAFE_API_KEY','')])
def test_disabled_or_unvalidated_model_never_applies_cached_jev_plan(tmp_path,monkeypatch,runtime,env,value):
    path=tmp_path/'news';prepare(TREND,path)
    runtime.reset_mock()
    monkeypatch.setenv(env,value)
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    assert prepare(TREND,path)==(FALLBACK,False)
    runtime.assert_not_called();sol.assert_called_once()
    assert not system_one.config()['applies_question_plans']


def test_simple_news_lookup_still_needs_zero_model_calls(tmp_path,monkeypatch,runtime):
    sol=Mock();monkeypatch.setattr('semantic.run_structured',sol)
    plan,_=prepare('최근 Physical AI 뉴스 알려줘',tmp_path/'news')
    assert plan['queries'][0]=='Physical AI'
    runtime.assert_not_called();sol.assert_not_called()


def test_curated_topic_retains_broad_research_template():
    question='Physical AI의 연구 현황에서 평가 방법과 데이터세트 구성, 해석의 한계는 무엇인가요?'
    intent,plan=candidate(question)
    assert intent=='research' and plan['queries'][0]==question and plan['axes'][0]==question


@pytest.mark.parametrize('failure',['timeout','rate_limit','invalid'])
def test_provider_failure_falls_back_and_shares_cooldown(tmp_path,monkeypatch,runtime,failure):
    def fail(body):
        if failure=='timeout':raise TimeoutError('must-not-log')
        if failure=='rate_limit':raise urllib.error.HTTPError(system_one.ENDPOINT,429,'must-not-log',{'Retry-After':'120'},None)
        return {'model':'wrong-model','answers':{}}
    runtime.side_effect=fail
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    path=tmp_path/'news';updates=[]
    assert prepare(TREND,path,report=updates.append)[0]==FALLBACK
    assert updates[-1]['reason'] in ('unavailable','rate_limit','invalid_response')
    assert prepare(PRIORITY,path,report=updates.append)[0]==FALLBACK
    assert updates[-1]['reason']=='cooldown'
    assert runtime.call_count==1 and sol.call_count==2
    assert b'must-not-log' not in system_one.db_path(path).read_bytes()


def test_busy_provider_has_bounded_wait_then_existing_fallback(tmp_path,monkeypatch,runtime):
    import fcntl
    sol=Mock(return_value=copy.deepcopy(FALLBACK));monkeypatch.setattr('semantic.run_structured',sol)
    with system_one.db_path(tmp_path/'a').with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        updates=[]
        assert prepare(TREND,tmp_path/'a',report=updates.append)[0]==FALLBACK
        assert updates[-1]['reason']=='busy'
        runtime.assert_not_called()
        assert sol.call_count==1
    assert prepare(PRIORITY,tmp_path/'b')[0]['queries'][0]==PRIORITY
    assert runtime.call_count==1


@pytest.mark.parametrize('count',[18,20])
def test_all_selected_evidence_is_classified_but_never_removed(tmp_path,runtime,count):
    evidence=[dict(id=str(i),text='원문의 조건과 비교 한계',title='제목 '+str(i),source_kind='paper',day='2026-10-15') for i in range(count)]
    original=copy.deepcopy(evidence)
    def unrelated(body):
        data=json.loads(body)
        assert data['state']['candidates']==[dict(index=i,**e) for i,e in enumerate(evidence)]
        assert len(data['questions'])==count
        return dict(model=data['model'],answers={k:dict(type='choice',choice='unrelated',confidence=1.0,
            probabilities={c:float(c=='unrelated') for c in q['criteria']}) for k,q in data['questions'].items()})
    runtime.side_effect=unrelated
    future=retrieval(tmp_path/'news',TREND,dict(axes=['변화','쟁점']),evidence,job_id='q')
    assert future.result(3)['status']=='complete' and evidence==original
    with sqlite3.connect(system_one.db_path(tmp_path/'news')) as db:
        baseline=json.loads(db.execute('select baseline_json from decision_observations').fetchone()[0])
    assert baseline['retained_ids']==[e['id'] for e in evidence]
    assert not system_one.config()['applies_evidence_selection']


def test_large_bundle_preserves_byte_guard_without_silent_truncation(tmp_path,runtime):
    evidence=[dict(id=str(i),text='긴 원문 '*1000) for i in range(20)]
    original=copy.deepcopy(evidence)
    future=retrieval(tmp_path/'news',TREND,{},evidence)
    assert future.result(3)['status']=='input_limit'
    assert evidence==original
    runtime.assert_not_called()


def test_empty_or_over_limit_bundle_does_not_call_provider(tmp_path,runtime):
    path=tmp_path/'news'
    assert retrieval(path,TREND,{},[]) is None
    assert retrieval(path,TREND,{},[dict(id=str(i)) for i in range(21)]) is None
    runtime.assert_not_called()
    with sqlite3.connect(system_one.db_path(path)) as db:
        reasons=[json.loads(r[0])['reason'] for r in db.execute('select result_json from decision_observations order by id')]
    assert reasons==['no_candidates','candidate_limit']


def test_progress_shows_plan_route_and_does_not_mix_jev_and_sol_estimates():
    from question_progress import setup,update,view,STAGES
    db=sqlite3.connect(':memory:');setup(db)
    db.execute('create table graph_question_jobs(id,status)')
    db.executemany('insert into graph_question_jobs values(?,?)',[('old','complete'),('new','running')])
    for stage in STAGES:update(db,'old',stage,done=True,phase='sol' if stage=='plan' else '')
    update(db,'new','evidence',done=True)
    update(db,'new','plan',phase='jev',routing=dict(method='jev',reason='evaluating'))
    result=view(db,'new','running',0)
    assert result['stages'][1]['label']=='Jev 질문 유형·계획 적합성 판단'
    assert result['remaining_seconds'] is None
    update(db,'new','plan',phase='sol',routing=dict(method='sol',reason='unavailable'))
    result=view(db,'new','running',0)
    assert 'Jev 연결이 지연' in result['planning_note']
    assert result['stages'][1]['label']=='Sol 질문 해석·검색 계획'
