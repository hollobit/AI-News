import copy
import io
import json
import threading
import time
import urllib.error
import pytest
import system_one as engine
from decision_tasks import choice


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','test-secret-never-log')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODE','shadow')
    monkeypatch.delenv('NEWS_SYSTEM_ONE_MODEL',raising=False)


def questions():
    return {'risk':choice('Sensitive risk?',{'required':'Yes','routine':'No','unknown':'Abstain'})}


def answer():
    return dict(model=engine.DEFAULT_MODEL,answers={'risk':dict(type='choice',choice='required',confidence=.85,
        probabilities={'required':.9,'routine':.05,'unknown':.05})},usage={'input_tokens':140,'output_tokens':20})


def test_exact_cache_invalidates_state_question_and_model(tmp_path,monkeypatch):
    calls=[]
    def transport(body):calls.append(json.loads(body));return dict(answer(),model=json.loads(body)['model'])
    path=tmp_path/'news';q=questions()
    first=engine.evaluate(path,'test',{'text':'상충 자료'},q,caller=transport)
    assert first['status']=='complete' and not first['cached']
    assert engine.evaluate(path,'test',{'text':'상충 자료'},q,caller=transport)['cached']
    engine.evaluate(path,'test',{'text':'변경 자료'},q,caller=transport)
    q['risk']['instructions']='Changed rubric'
    engine.evaluate(path,'test',{'text':'변경 자료'},q,caller=transport)
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODEL','jev-1.13.0')
    engine.evaluate(path,'test',{'text':'변경 자료'},q,caller=transport)
    assert len(calls)==4
    assert not engine.status(path)['applies_decisions']
    assert b'test-secret-never-log' not in engine.db_path(path).read_bytes()


@pytest.mark.parametrize('mutate',[
    lambda r:r.update(model='jev-latest'),
    lambda r:r['answers'].clear(),
    lambda r:r['answers']['risk'].update(choice='invented'),
    lambda r:r['answers']['risk'].update(choice='routine'),
    lambda r:r['answers']['risk'].update(confidence=float('nan')),
    lambda r:r['answers']['risk']['probabilities'].update(required=True),
    lambda r:r['answers']['risk']['probabilities'].update(required=.3),
])
def test_invalid_typed_answers_fall_back_without_exposing_payload(tmp_path,mutate):
    result=answer();mutate(result)
    value=engine.evaluate(tmp_path/'news','test','source',questions(),caller=lambda body:result)
    assert value['status']=='invalid_response' and 'answers' not in value


def test_noul_has_no_confidence_and_is_not_event_probability():
    value=engine.validate(dict(model=engine.DEFAULT_MODEL,answers={'a':{'type':'noul','noul':.7}}),
                          {'a':{'type':'noul','instructions':'Flag?'}},engine.DEFAULT_MODEL)
    assert value['answers']['a']=={'type':'noul','noul':.7}


def test_retry_after_is_shared_and_no_synchronous_retries(tmp_path):
    calls=[];path=tmp_path/'news'
    def limited(body):
        calls.append(body)
        raise urllib.error.HTTPError(engine.ENDPOINT,429,'private',{'Retry-After':'120'},io.BytesIO(b'secret provider body'))
    assert engine.evaluate(path,'test','source',questions(),caller=limited)['status']=='rate_limit'
    wait=engine.evaluate(path,'test','changed',questions(),caller=limited)
    assert wait['status']=='cooldown' and wait['retry_at']>time.time()+100
    assert len(calls)==1
    assert b'secret provider body' not in engine.db_path(path).read_bytes()


def test_timeout_and_missing_key_never_block_primary_analysis(tmp_path,monkeypatch):
    def timeout(body):raise TimeoutError('secret')
    assert engine.evaluate(tmp_path/'news','test','source',questions(),caller=timeout)['status']=='unavailable'
    monkeypatch.delenv('TYPESAFE_API_KEY')
    assert engine.observe(tmp_path/'news','test','source',questions()) is None
    assert engine.evaluate(tmp_path/'news','test','source',questions())['status']=='disabled'
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODEL','jev-latest')
    assert engine.config()['mode']=='invalid'


def test_shadow_has_one_pending_task_and_cannot_change_baseline(tmp_path,monkeypatch):
    entered=threading.Event();release=threading.Event()
    baseline={'path':'multi-role'};original=copy.deepcopy(baseline)
    def transport(body):entered.set();assert release.wait(2);return answer()
    monkeypatch.setattr(engine,'transport',transport)
    future=engine.observe(tmp_path/'news','test','source',questions(),baseline)
    try:
        assert entered.wait(2)
        assert engine.observe(tmp_path/'news','test','more',questions(),baseline) is None
        assert baseline==original
    finally:release.set()
    assert future.result(3)['status']=='complete'
    assert baseline==original


def test_holdout_evaluation_rejects_event_leakage_and_counts_dangerous_errors(tmp_path):
    from system_one_eval import run
    row=dict(id='a',group='event-a',split='holdout',purpose='test',state='source',questions=questions(),expected={'risk':'required'})
    with pytest.raises(ValueError):run([row,dict(row,id='b')],tmp_path/'news')
    wrong=answer();wrong['answers']['risk'].update(choice='routine',probabilities={'required':.05,'routine':.9,'unknown':.05})
    result=run([row],tmp_path/'news',caller=lambda body:wrong)
    assert result['unsafe_choices']==1 and result['accuracy']==0
    assert not result['activation_allowed']
