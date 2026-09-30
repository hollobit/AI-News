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


def test_access_preflight_caches_failure_and_never_downgrades(tmp_path):
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


def test_cli_update_invalidates_cached_model_rejection(monkeypatch,tmp_path):
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
