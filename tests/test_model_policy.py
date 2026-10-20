import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from model_policy import policy
from semantic import run_structured
from llm_runtime import runtime_status


def test_role_routing_and_strict_schema_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'runtime.db'))
    monkeypatch.setenv('NEWS_MODEL_LUNA','gpt-6-luna')
    monkeypatch.setenv('NEWS_MODEL_SOL','gpt-6.1-sol')
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    models=[]
    def execute(command, **kwargs):
        models.append(command[command.index('--model')+1])
        Path(command[command.index('--output-last-message')+1]).write_text('{"ok":true}')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr('semantic.subprocess.run',execute)
    for role,escalation,model in [('baseline_analysis',False,'gpt-6-luna'),
                                  ('baseline_verification',False,'gpt-6.1-sol'),
                                  ('risk_verification',True,'gpt-6-astra')]:
        result=run_structured('test',{'type':'object'},role=role,escalation=escalation)
        assert result=={'ok':True}
        assert result.provenance['model']==model
        assert len(result.provenance['input_hash'])==64
        assert 'provenance' not in json.dumps(result)
    assert models==['gpt-6-luna','gpt-6.1-sol','gpt-6-astra']
    assert {r['model'] for r in runtime_status(tmp_path/'runtime.db')['recent']}==set(models)


def test_outage_does_not_fall_back_to_another_model(monkeypatch,tmp_path):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'runtime.db'))
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    calls=[]
    def fail(*a,**k):
        calls.append(a)
        return SimpleNamespace(returncode=1,stderr='usage limit')
    monkeypatch.setattr('semantic.subprocess.run',fail)
    with pytest.raises(RuntimeError,match='rate_limit'):run_structured('test',{},role='baseline_analysis')
    assert len(calls)==1


def test_latest_models_do_not_allow_older_env_override(monkeypatch):
    monkeypatch.setenv('NEWS_MODEL_LUNA','gpt-5.6-luna')
    with pytest.raises(ValueError):policy('baseline_analysis')


def test_only_question_drafts_use_luna_and_review_and_repair_remain_sol():
    from llm_runtime import INTERACTIVE_ROLES
    assert policy('graph_draft')['model']=='gpt-6-luna'
    assert policy('graph_draft')['reasoning_effort']=='medium'
    assert policy('graph_answer')['model']=='gpt-6.1-sol'
    assert 'graph_draft' in INTERACTIVE_ROLES


@pytest.mark.parametrize('question,axes,expected',[
    ('Physical AI 벤치마크와 데이터세트 차이가 성능 해석에 주는 의미는?',3,'graph_draft'),
    ('A와 B의 비교',2,'graph_draft'),
    ('최근 보안 사고 유형은?',2,'graph_answer'),
    ('벤치마크 차이에 따른 대응 전략은?',3,'graph_answer'),
    ('데이터세트와 성능 비교',4,'graph_answer'),
    ('데이터세트와 성능 비교',5,'graph_answer'),
    ('데이터세트와 성능 비교',0,'graph_answer'),
])
def test_question_routing_keeps_broad_or_unplanned_synthesis_on_sol(question,axes,expected):
    from model_policy import question_draft_role
    assert question_draft_role(question,{'axes':['축']*axes})==expected


def test_four_axis_overviews_are_bounded_and_interpretation_remains_sol():
    from model_policy import question_draft_role
    plan={'axes':['연구 현황','데이터 종류','이슈','자료 간 통합']}
    evidence=[dict(text='x'*2000) for _ in range(24)]
    overview='최근 Physical AI 벤치마크 연구 현황과 데이터세트 종류, 이슈는?'
    assert question_draft_role(overview,plan,evidence=evidence)=='graph_draft'
    for question in ('벤치마크별 평가 방식과 데이터세트 구성이 성능 수치 해석에 주는 의미는?',
                     '벤치마크 종류별 성능 차이의 원인은?',
                     '벤치마크 연구 현황을 바탕으로 대응 전략은?',
                     '벤치마크 방법과 데이터 구성 비교'):
        assert question_draft_role(question,plan,evidence=evidence)=='graph_answer'
    assert question_draft_role(overview,plan)=='graph_answer'
    assert question_draft_role(overview,plan,evidence=[])=='graph_answer'
    assert question_draft_role(overview,plan,evidence=evidence+[dict(text='x')])=='graph_draft'
    assert question_draft_role(overview,plan,evidence=[dict(text='x'*48001)])=='graph_draft'
    assert question_draft_role(overview,{'axes':['축']*5},evidence=evidence)=='graph_answer'


def test_access_preflight_caches_failure_and_never_downgrades(tmp_path,monkeypatch):
    monkeypatch.setenv("NEWS_LLM_RUNTIME_DB",str(tmp_path/"runtime.db"))
    from model_access import ensure_model_access
    from engine_errors import EngineError
    calls=[]
    def unavailable(prompt,schema,**kwargs):
        calls.append(kwargs)
        if not kwargs['escalation']:raise EngineError('configuration')
        return {'ok':True}
    path=tmp_path/'access.json'
    result=ensure_model_access(caller=unavailable,state_path=path,stamp=1000)
    assert not result['ready']
    assert result['models']==['gpt-6-luna','gpt-6.1-sol','gpt-6-astra']
    assert [c['ok'] for c in result['checks']]==[False,False,True]
    assert ensure_model_access(caller=unavailable,state_path=path,stamp=1001)==result
    assert len(calls)==3
    assert all(c['reasoning_effort']=='low' for c in calls)


def test_cli_update_invalidates_cached_model_rejection(monkeypatch,tmp_path):
    monkeypatch.setenv("NEWS_LLM_RUNTIME_DB",str(tmp_path/"runtime.db"))
    from model_access import ensure_model_access
    from engine_errors import EngineError
    version={'version':'old'}
    monkeypatch.setattr('model_access.client_identity',lambda:dict(version))
    calls=[]
    def probe(*args,**kwargs):
        calls.append(kwargs)
        if version['version']=='old':raise EngineError('configuration')
        return {'ok':True}
    path=tmp_path/'access.json'
    assert not ensure_model_access(caller=probe,state_path=path,stamp=1000)['ready']
    version['version']='new'
    assert ensure_model_access(caller=probe,state_path=path,stamp=1001)['ready']
    assert len(calls)==6


def test_announcement_execution_matches_policy_and_restores_wire_ids(monkeypatch,tmp_path):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'runtime.db'))
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    identity='news_'+'a'*24
    prompt='ROLE: integrated_analysis\nDATA:\n'+json.dumps(dict(evidence=[dict(id=identity,text='새 도구 공개',origin='telegram_excerpt')],coverage={'failed_urls':[]}))
    def execute(command,**kwargs):
        assert command[command.index('--model')+1]=='gpt-6-luna'
        assert 'model_reasoning_effort="medium"' in command
        data=json.loads(kwargs['input'].split('DATA:\n')[1])
        assert data['evidence'][0]['id']=='E1'
        Path(command[command.index('--output-last-message')+1]).write_text('{"evidence_ids":["E1"]}')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr('semantic.subprocess.run',execute)
    result=run_structured(prompt,{'type':'object'})
    assert result=={'evidence_ids':[identity]}
    assert result.provenance['policy_version']=='news-models-announcement-v1'
    assert result.provenance['role']=='integrated_analysis'
    assert result.provenance['wire_format']=='short-evidence-ids-v1'


def test_analysis_instructions_keep_model_review_and_isolation(monkeypatch,tmp_path):
    import tomllib
    from semantic import QUESTION_INSTRUCTIONS
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'runtime.db'))
    monkeypatch.setenv('TELEGRAM_API_HASH','test-private-secret')
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    instruction_files=[]
    def execute(command,**kwargs):
        assert command[command.index('--model')+1]=='gpt-6.1-sol'
        assert 'model_reasoning_effort="medium"' in command
        assert '--ignore-user-config' in command and '--ephemeral' in command
        assert command[command.index('--sandbox')+1]=='read-only'
        assert 'TELEGRAM_API_HASH' not in kwargs['env']
        instructions=[a for a in command if a.startswith('model_instructions_file=')]
        if instructions:
            file=Path(tomllib.loads(instructions[0])['model_instructions_file'])
            assert file.read_text()==QUESTION_INSTRUCTIONS
            assert 'independently check the complete answer' in file.read_text()
            instruction_files.append(file)
        else:raise AssertionError("Analysis instructions missing")
        Path(command[command.index('--output-last-message')+1]).write_text('{"ok":true}')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr('semantic.subprocess.run',execute)
    question=run_structured('question',{},role='graph_answer')
    other=run_structured('other',{},role='baseline_verification')
    assert question.provenance['instruction_profile']=='grounded-analysis-v1'
    assert other.provenance['instruction_profile']=='grounded-analysis-v1'
    assert instruction_files and all(not p.exists() for p in instruction_files)


def test_question_speed_is_opt_in_and_never_changes_background_or_probe(monkeypatch):
    from model_policy import execution_tier
    monkeypatch.delenv('NEWS_QUESTION_SERVICE_TIER',raising=False)
    original=policy('graph_answer')
    assert execution_tier('graph_answer')=='standard'
    monkeypatch.setenv('NEWS_QUESTION_SERVICE_TIER','fast')
    assert execution_tier('strategic_question_verification')=='fast'
    assert execution_tier('graph_answer')=='standard'
    assert execution_tier('graph_draft')=='standard'
    assert policy('graph_answer')==original
    for role in ('engine_probe','baseline_analysis','baseline_verification','paper_analysis','risk_verification','wiki_compile','strategic_forecast'):
        assert execution_tier(role)=='standard'
    monkeypatch.setenv('NEWS_QUESTION_SERVICE_TIER','ultrafast')
    with pytest.raises(ValueError,match='NEWS_QUESTION_SERVICE_TIER'):execution_tier('graph_answer')
    assert execution_tier('baseline_analysis')=='standard'


def test_question_speed_local_setting_preserves_explicit_process_override(monkeypatch,tmp_path):
    from app import load_local_env
    from model_policy import execution_tier
    env=tmp_path/'settings';env.write_text('NEWS_QUESTION_SERVICE_TIER=fast\n')
    monkeypatch.delenv('NEWS_QUESTION_SERVICE_TIER',raising=False)
    load_local_env(env)
    assert execution_tier('strategic_question_verification')=='fast'
    monkeypatch.setenv('NEWS_QUESTION_SERVICE_TIER','standard')
    load_local_env(env)
    assert execution_tier('strategic_question_verification')=='standard'


@pytest.mark.parametrize('role,fast,model',[
    ('graph_draft',False,'gpt-6-luna'),('graph_answer',False,'gpt-6.1-sol'),
    ('strategic_question_verification',True,'gpt-6.1-sol'),
    ('baseline_verification',False,'gpt-6.1-sol')])
def test_question_speed_reaches_cli_and_content_free_provenance(monkeypatch,tmp_path,role,fast,model):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_QUESTION_SERVICE_TIER','fast')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'runtime.db'))
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    def execute(command,**kwargs):
        assert ('service_tier="fast"' in command)==fast
        assert ('--strict-config' in command)==fast
        assert ('fast_mode' in command)==fast
        assert command[command.index('--model')+1]==model
        assert 'model_reasoning_effort="medium"' in command
        assert '--ignore-user-config' in command and '--ephemeral' in command
        assert 'NEWS_QUESTION_SERVICE_TIER' not in kwargs['env']
        Path(command[command.index('--output-last-message')+1]).write_text('{"ok":true}')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr('semantic.subprocess.run',execute)
    result=run_structured('private original excerpt',{},role=role)
    requested='fast' if fast else 'standard'
    assert result.provenance['service_tier_requested']==requested
    row=runtime_status()['recent'][0]
    assert row['service_tier_requested']==requested
    assert row['model']==model and row['reasoning_effort']=='medium'
    assert 'private original excerpt' not in json.dumps(row)
    assert 'service_tier_actual' not in row


def test_fast_mode_failure_never_retries_on_different_tier_or_model(monkeypatch,tmp_path):
    from engine_errors import EngineError
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_QUESTION_SERVICE_TIER','fast')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'runtime.db'))
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    calls=[]
    def execute(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1,stderr='ERROR: rate limit')
    monkeypatch.setattr('semantic.subprocess.run',execute)
    with pytest.raises(EngineError,match='rate_limit'):run_structured('question',{},role='strategic_question_verification')
    assert len(calls)==1 and 'service_tier="fast"' in calls[0]
    row=runtime_status()['recent'][0]
    assert row['status']=='failed' and row['error_code']=='rate_limit'
    assert row['service_tier_requested']=='fast'


@pytest.mark.parametrize('count,characters,expected',[(24,48000,'medium'),(25,48000,'high'),(24,48001,'high'),(32,64000,'high'),(33,64000,None),(32,64001,None)])
def test_extended_overview_capacity_and_effort(count,characters,expected):
    from model_policy import question_draft_role, question_draft_effort
    evidence=[{'text':''} for _ in range(count)]
    evidence[0]['text']='x'*characters
    question='벤치마크 연구 현황과 데이터세트 종류는?'
    role=question_draft_role(question,{'axes':['방법','종류','이슈']},evidence=evidence)
    assert role==('graph_draft' if expected else 'graph_answer')
    assert question_draft_effort(role,evidence=evidence)==(expected or 'medium')
    if expected=='high':
        for suffix in (' 해석은?', ' 왜 차이가 나는가?', ' 대응 전략은?'):
            assert question_draft_role(question+suffix,{'axes':['방법','종류','이슈']},evidence=evidence)=='graph_answer'
    assert question_draft_effort('graph_answer',evidence=evidence)=='medium'
