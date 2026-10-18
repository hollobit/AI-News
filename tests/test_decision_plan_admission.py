from contextlib import contextmanager
import json
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest

import system_one
from graph_plans import prepare
from test_graph_plan_router import TREND, PRIORITY, FALLBACK, response


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODE','shadow')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODEL',system_one.DEFAULT_MODEL)
    monkeypatch.setenv('NEWS_QUESTION_PLAN_MODE','jev')
    monkeypatch.setenv('TYPESAFE_API_KEY','test-key-never-log')


@contextmanager
def provider_lock(path):
    # A real separate process owns the same provider lock as a shadow worker.
    code='import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.readline()'
    child=subprocess.Popen([sys.executable,'-c',code,str(system_one.db_path(path).with_suffix('.lock'))],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        yield child
    finally:
        if child.poll() is None:
            child.stdin.write('\n');child.stdin.flush()
        child.wait(timeout=3)
        child.stdin.close();child.stdout.close()


def test_cached_plan_bypasses_shadow_and_provider_ownership_without_network(tmp_path,monkeypatch):
    path=tmp_path/'news';transport=Mock(side_effect=response)
    monkeypatch.setattr(system_one,'transport',transport)
    expected=prepare(TREND,path)[0]
    with sqlite3.connect(path) as db:db.execute('DELETE FROM graph_question_plans')
    with sqlite3.connect(system_one.db_path(path)) as db:
        db.execute("INSERT OR REPLACE INTO provider_wait VALUES(1,?,'rate_limit')",(time.time()+60,))
    assert system_one.PENDING.acquire(blocking=False)
    try:
        with provider_lock(path):
            # A cache hit must not even attempt/wait for the provider lock.
            monkeypatch.setattr(system_one,'_admit',Mock(side_effect=AssertionError('cached call waited')))
            monkeypatch.setattr('semantic.run_structured',Mock(side_effect=AssertionError('unexpected Sol fallback')))
            updates=[]
            assert prepare(TREND,path,report=updates.append)[0]==expected
        assert updates[-1]['decision_cached'] and transport.call_count==1
        with sqlite3.connect(system_one.db_path(path)) as db:
            status,cached,result=db.execute('SELECT status,cached,result_json FROM decision_observations ORDER BY id DESC LIMIT 1').fetchone()
            assert status=='complete' and cached==1 and json.loads(result)['application']['applied']
            assert db.execute('SELECT until FROM provider_wait').fetchone()[0]>time.time()
    finally:system_one.PENDING.release()


def test_short_cross_process_contention_does_not_invoke_sol(tmp_path,monkeypatch):
    path=tmp_path/'news';transport=Mock(side_effect=response)
    monkeypatch.setattr(system_one,'transport',transport)
    monkeypatch.setattr('semantic.run_structured',Mock(side_effect=AssertionError('avoidable Sol fallback')))
    with provider_lock(path) as child:
        release=threading.Timer(.05,lambda:(child.stdin.write('\n'),child.stdin.flush()))
        release.start()
        try:
            plan,_=prepare(TREND,path)
        finally:release.join()
    assert plan['queries'][0]==TREND and transport.call_count==1


def test_long_contention_returns_once_without_provider_retries(tmp_path,monkeypatch):
    path=tmp_path/'news';transport=Mock(side_effect=response);sol=Mock(return_value=FALLBACK)
    monkeypatch.setattr(system_one,'transport',transport);monkeypatch.setattr('semantic.run_structured',sol)
    with provider_lock(path):
        started=time.monotonic();updates=[]
        assert prepare(TREND,path,report=updates.append)[0]==FALLBACK
        assert .20<=time.monotonic()-started<1
    assert transport.call_count==0 and sol.call_count==1 and updates[-1]['reason']=='busy'


def test_cache_is_rechecked_after_owner_finishes(tmp_path,monkeypatch):
    path=tmp_path/'news';entered=threading.Event();release=threading.Event();results=[]
    calls=[]
    def transport(body):
        calls.append(body);entered.set();assert release.wait(2);return response(body)
    monkeypatch.setattr(system_one,'transport',transport)
    monkeypatch.setattr('semantic.run_structured',Mock(side_effect=AssertionError('unexpected Sol fallback')))
    # Two separate threads exercise the real lock and durable cache. Different
    # plan storage is not needed: both enter before the first result is saved.
    first=threading.Thread(target=lambda:results.append(prepare(TREND,path)[0]))
    first.start()
    try:
        assert entered.wait(2)
        timer=threading.Timer(.05,release.set);timer.start()
        results.append(prepare(TREND,path)[0]);timer.join()
    finally:release.set();first.join(3)
    assert len(calls)==1 and len(results)==2 and results[0]==results[1]


def test_query_never_overlaps_a_running_shadow_request(tmp_path,monkeypatch):
    from decision_tasks import choice
    path=tmp_path/'news';entered=threading.Event();release=threading.Event();active=[]
    def transport(body):
        active.append(json.loads(body));entered.set();assert release.wait(3)
        return dict(model=system_one.DEFAULT_MODEL,answers={'kind':dict(type='choice',choice='yes',confidence=1,probabilities={'yes':1,'no':0})})
    monkeypatch.setattr(system_one,'transport',transport)
    monkeypatch.setattr('semantic.run_structured',Mock(return_value=FALLBACK))
    future=system_one.observe(path,'workflow','source',{'kind':choice('Check',dict(yes='Yes',no='No'))})
    try:
        assert entered.wait(2)
        assert prepare(TREND,path)[0]==FALLBACK
        assert len(active)==1
    finally:release.set()
    assert future.result(3)['status']=='complete'


@pytest.mark.parametrize('invalid',['expired','changed_question','corrupt'])
def test_cached_decision_cannot_bypass_exactness_or_validation(tmp_path,monkeypatch,invalid):
    path=tmp_path/'news';transport=Mock(side_effect=response);sol=Mock(return_value=FALLBACK)
    monkeypatch.setattr(system_one,'transport',transport);monkeypatch.setattr('semantic.run_structured',sol)
    prepare(TREND,path)
    with sqlite3.connect(path) as db:db.execute('DELETE FROM graph_question_plans')
    with sqlite3.connect(system_one.db_path(path)) as db:
        if invalid=='expired':db.execute('UPDATE decisions SET created_at=?',(time.time()-8*86400,))
        if invalid=='corrupt':db.execute("UPDATE decisions SET result='{}'")
    with provider_lock(path):
        updates=[]
        assert prepare(PRIORITY if invalid=='changed_question' else TREND,path,report=updates.append)[0]==FALLBACK
    assert sol.call_count==1 and transport.call_count==1
    assert updates[-1]['reason'] in ('busy','invalid_input')
