import io
import json
from types import SimpleNamespace
from pathlib import Path
import pytest
from llm_usage import read_usage,totals,failure_code
from llm_runtime import LLMRuntime,runtime_status


def test_usage_is_numeric_only_optional_fields_stay_unknown():
    events=[{'type':'item.completed','item':{'text':'private article'}},
            {'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':50,'output_tokens':20,'reasoning_output_tokens':5}},
            {'type':'turn.completed','usage':{'input_tokens':80,'output_tokens':10}}]
    usage=read_usage(io.StringIO('\n'.join(map(json.dumps,events))))
    assert usage==dict(input_tokens=180,cached_input_tokens=None,output_tokens=30,reasoning_output_tokens=None)
    assert read_usage(io.StringIO('{bad\n{"type":"turn.completed","usage":{"input_tokens":true,"output_tokens":1}}'))=={}
    assert read_usage(io.StringIO(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':2,'output_tokens':1}})))=={}


def test_numeric_usage_survives_success_and_failure_without_inventing_old_counts(tmp_path):
    runtime=LLMRuntime(tmp_path/'db',limit=1)
    with runtime.slot('verification',2,2) as t:t.usage=dict(input_tokens=100,cached_input_tokens=50,output_tokens=20,reasoning_output_tokens=5)
    with pytest.raises(ValueError):
        with runtime.slot('verification',2,2) as t:
            t.usage=dict(input_tokens=10,output_tokens=4)
            raise ValueError('private diagnostic')
    with runtime.slot('verification',2,2):pass
    state=runtime_status(runtime.path)
    assert state['usage']==dict(measured_calls=2,input_tokens=110,cached_input_tokens=None,output_tokens=24,reasoning_output_tokens=None)
    assert 'private diagnostic' not in str(state)


def test_cli_json_records_usage_and_safe_error_code(monkeypatch,tmp_path):
    from semantic import run_structured
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    monkeypatch.setenv('NEWS_LLM_RUNTIME_DB',str(tmp_path/'db'))
    monkeypatch.setattr('semantic.codex_executable',lambda:'codex')
    def execute(command,**kwargs):
        assert '--json' in command
        kwargs['stdout'].write(json.dumps({'type':'turn.completed','usage':{'input_tokens':200,'cached_input_tokens':0,'output_tokens':20,'reasoning_output_tokens':3}})+'\n')
        Path(command[command.index('--output-last-message')+1]).write_text('{"ok":true}')
        return SimpleNamespace(returncode=0,stderr='')
    monkeypatch.setattr('semantic.subprocess.run',execute)
    result=run_structured('test',{},role='engine_probe')
    assert result.provenance['usage']['input_tokens']==200
    def fail(command,**kwargs):
        kwargs['stdout'].write('{"type":"error","message":"usage limit"}\n')
        return SimpleNamespace(returncode=1,stderr='')
    monkeypatch.setattr('semantic.subprocess.run',fail)
    with pytest.raises(RuntimeError,match='rate_limit'):run_structured('test',{},role='engine_probe')
    assert failure_code(io.StringIO('{"type":"item.completed","item":{"text":"usage limit"}}')) is None
