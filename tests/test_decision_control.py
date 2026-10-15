import json
import os
import sqlite3
import time
import urllib.error

import pytest
import system_one
from decision_journal import context, path_for, source_refs
from server_routes.decision_control import detail, overview


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','never-record-this-test-key')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODE','shadow')
    monkeypatch.setenv('NEWS_SYSTEM_ONE_MODEL','jev-1.14.0')
    path=tmp_path/'news.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE strategic_workflow_runs(id PRIMARY KEY,status,created_at,updated_at,snapshot_json,request_json);
            CREATE TABLE strategic_workflow_events(seq INTEGER PRIMARY KEY,run_id,stage,status,created_at);
            CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json);''')
        for identity in ('a','b'):
            db.execute('INSERT INTO strategic_workflow_runs VALUES(?,?,?,?,?,?)',(identity,'complete','2026-10-15T01:00:00Z','2026-10-15T01:01:00Z',json.dumps([{'id':'n1','title':'한국어 기사 '+identity,'url':'https://example.org/article?id=1&section=ai','text':'private source body'}]),'{}'))
        for seq,stage,status in [(1,'collection','complete'),(2,'integrated_analysis','running'),(3,'integrated_analysis','failed'),(4,'integrated_analysis','running'),(5,'integrated_analysis','complete'),(6,'integrated_verification','complete'),(7,'final','complete')]:
            db.execute('INSERT INTO strategic_workflow_events VALUES(?,?,?,?,?)',(seq,'a',stage,status,f'2026-10-15T01:00:0{seq}Z'))
        db.execute("INSERT INTO strategic_workflow_artifacts VALUES('a','integrated_analysis',?)",(json.dumps({'summary':'UNREVIEWED DRAFT MUST NOT LEAK'}),))
    return path


def evaluate(path, subject_id='a', caller=None):
    q={'risk':dict(type='choice',instructions='Is risk review required?',criteria={'required':'Yes','routine':'No'})}
    def answer(body):
        return dict(model='jev-1.14.0',answers={'risk':dict(type='choice',choice='required',probabilities={'required':.95,'routine':.05},confidence=.9)},usage={'input_tokens':100,'output_tokens':10})
    return system_one.evaluate(path,'workflow',{'text':'private source body'},q,{'path':'parallel-drafts-v1','secret':'never-record-baseline'},
        subject=context('workflow',subject_id,'한국어 기사',[{'id':'n1','title':'기사','source_url':'https://example.org/article?id=1&section=ai'}]),caller=caller or answer)


def test_exact_cache_attaches_to_each_subject_and_keeps_attempts(store):
    assert evaluate(store)['cached'] is False
    assert evaluate(store,'b')['cached'] is True
    assert evaluate(store)['cached'] is True
    first=detail(store,'workflow','a');second=detail(store,'workflow','b')
    assert len(first['decisions'])==2 and len(second['decisions'])==1
    assert [d['cached'] for d in first['decisions']]==[0,1]
    assert second['decisions'][0]['subject_id']=='b'
    assert first['sources'][0]['url'].endswith('?id=1&section=ai')
    assert [e['status'] for e in first['events'] if e['stage']=='integrated_analysis']==['running','failed','running','complete']
    output=json.dumps(first)
    assert 'UNREVIEWED DRAFT' not in output and 'private source body' not in output
    raw=path_for(store).read_bytes()
    assert b'never-record-this-test-key' not in raw and b'private source body' not in raw and b'never-record-baseline' not in raw
    mtimes=[p.stat().st_mtime_ns for p in (store,path_for(store))]
    summary=overview(store)
    assert summary['metrics']['calls']==1 and summary['metrics']['cached']==2
    assert summary['metrics']['input_tokens']==100
    assert not summary['settings']['applies_workflow_decisions']
    assert not summary['settings']['applies_evidence_selection']
    assert mtimes==[p.stat().st_mtime_ns for p in (store,path_for(store))]


def test_failure_cooldown_and_admission_skips_visible_without_retries(store):
    calls=[]
    def limited(body):
        calls.append(1)
        raise urllib.error.HTTPError(system_one.ENDPOINT,429,'secret provider message',{'Retry-After':'120'},None)
    assert evaluate(store,caller=limited)['status']=='rate_limit'
    assert evaluate(store,caller=limited)['status']=='cooldown'
    system_one.skipped(store,'workflow','pending_limit',context('workflow','a','title'))
    records=detail(store,'workflow','a')['decisions']
    assert [r['status'] for r in records]==['rate_limit','cooldown','skipped']
    assert len(calls)==1 and overview(store)['metrics']['calls']==0
    assert records[1]['result']['retry_at']>time.time()+100
    assert 'secret provider message' not in json.dumps(records)


def test_missing_and_dead_owner_are_not_running_or_complete(store):
    assert detail(store,'workflow','missing') is None
    with sqlite3.connect(store) as db:
        db.execute("UPDATE strategic_workflow_runs SET status='running',request_json='{}' WHERE id='a'")
    assert detail(store,'workflow','a')['job']['status']=='owner_missing'
    assert not path_for(store).exists()
    assert overview(store)['decisions']==[]
    assert not path_for(store).exists()


def test_source_refs_preserve_query_and_reject_credentials_or_script_urls():
    items=source_refs([{'id':'n','source_url':u} for u in ('https://example.org/?id=7&part=a%26b','javascript:alert(1)','https://user:secret@example.org/')])
    assert items[0]['url']=='https://example.org/?id=7&part=a%26b'
    assert [i['url'] for i in items[1:]]==['','']


def test_question_repair_history_survives_progress_reset(tmp_path):
    from question_progress import setup,update
    path=tmp_path/'news.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE graph_question_jobs(id PRIMARY KEY,status,created_at,owner_pid,request_json)')
        db.execute('INSERT INTO graph_question_jobs VALUES(?,?,?,?,?)',('q','running',time.time(),os.getpid(),'{"question":"한국어 질의"}'))
        setup(db)
        update(db,'q','review')
        update(db,'q','review',phase='repair')
        update(db,'q','review',phase='recheck')
        update(db,'q','evidence',reset=True)
    data=detail(path,'question','q')
    assert [e['stage'] for e in data['events']]==['review','review_repair','review_recheck','evidence']
    assert data['job']['href']=='/context?answer=q'


def test_journal_storage_failure_never_blocks_evaluation(store,monkeypatch):
    import decision_journal
    def full(*a,**kw):raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(decision_journal,'begin',full)
    assert evaluate(store)['status']=='complete'
