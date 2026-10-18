from datetime import datetime,timezone,timedelta
import json
import os
import sqlite3
from unittest.mock import patch

import pytest
from bulk_baseline import freeze_item
from baseline_source_window import wait_for_sources,headline_only,deep_owner
from scheduled_collection import dispatch

STAMP=datetime(2026,10,18,9,2,38,tzinfo=timezone.utc)


@pytest.fixture
def db():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE bulk_baseline_runs(id,status,owner_pid,created_at,updated_at);
    CREATE TABLE bulk_baseline_documents(run_id,document_id,input_hash,status);
    CREATE TABLE bulk_baseline_cache(input_hash,result_json);
    CREATE TABLE bulk_baseline_events(seq INTEGER PRIMARY KEY,run_id,stage,detail,created_at);
    CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json,updated_at);
    CREATE TABLE risk_schedule(id,enabled,launched_pid,cycle_id);
    CREATE TABLE rsi_cycles(id,owner_pid,pause_requested,created_at,status,error);''')
    db.executemany('INSERT INTO state VALUES (?,?)',[
        ('collector_last_success',STAMP.isoformat()),
        ('collector_corpus_snapshot',json.dumps({'status':'complete','extracted_at':STAMP.isoformat()}))])
    db.execute('INSERT INTO risk_schedule VALUES(1,1,?,?)',(os.getpid(),'deep'))
    db.execute("INSERT INTO rsi_cycles VALUES(?,?,0,?,'running','')",('deep',os.getpid(),STAMP.isoformat()))
    yield db
    db.close()


def item(name='a',text=None):
    return dict(title='AI 도구 발표 '+name,text=text or 'AI 도구 발표 '+name+' https://example.org/'+name,
                source_url='https://example.org/'+name)


def arrival(db,name='a',status='fetched'):
    db.execute('INSERT OR REPLACE INTO source_excerpts VALUES(?,?,?)',
               ('https://example.org/'+name,json.dumps({'status':status,'text':'새로 확보한 작동 조건과 실제 기능의 발췌'}),STAMP.isoformat()))


class Clock:
    def __init__(self,action=lambda _:None):self.elapsed=0;self.action=action
    def __call__(self):return self.elapsed
    def sleep(self,seconds):self.elapsed+=seconds;self.action(self.elapsed)


def wait(db,items=None,clock=None,**changes):
    snapshots={s['document_id']:s for s in map(freeze_item,items or [item()])}
    frozen=json.dumps(snapshots,sort_keys=True)
    clock=clock or Clock()
    args=dict(pending=set(snapshots),prior={},extraction={'extracted_at':STAMP.isoformat()},now=STAMP,
              clock=clock,sleep=clock.sleep,owner=lambda _:os.getpid())
    args.update(changes)
    result=wait_for_sources(db,snapshots,**args)
    assert json.dumps(snapshots,sort_keys=True)==frozen
    return result


def test_sources_coalesce_once_without_changing_frozen_input(db):
    progress=[]
    clock=Clock(lambda seconds:arrival(db,'a' if seconds<4 else 'b') if seconds>=2 else None)
    with patch('semantic.run_structured') as model:
        result=wait(db,[item('a'),item('b')],clock,progress=progress.append)
    model.assert_not_called()
    assert result['reason']=='sources_ready' and result['elapsed_seconds']==4
    assert result['ready_documents']==2 and result['pending_documents']==0
    assert progress[0]['remaining_seconds']==45 and progress[-1]['remaining_seconds']==42
    assert progress[-1]['ready_documents']==1
    assert 'example.org' not in json.dumps(progress)


@pytest.mark.parametrize('elapsed,expected',[(0,45),(10,35),(40,5)])
def test_event_deadline_cannot_restart_when_process_restarts(db,elapsed,expected):
    result=wait(db,now=STAMP+timedelta(seconds=elapsed))
    assert result['reason']=='deadline' and result['elapsed_seconds']==expected
    assert result['pending_documents']==1


@pytest.mark.parametrize('age',[-1,45,300])
def test_outside_fresh_window_does_not_delay(db,age):
    assert wait(db,now=STAMP+timedelta(seconds=age)) is None


def test_meaningful_messages_and_mixed_batches_are_not_held(db):
    rich=item('rich','현장 조건과 측정 결과를 확인하기 위한 새로운 실험 및 평가 방법을 발표했습니다. '*5)
    assert not headline_only(freeze_item(rich))
    assert wait(db,[rich]) is None
    assert wait(db,[item(),rich]) is None
    assert wait(db,[item(str(i)) for i in range(65)]) is None


@pytest.mark.parametrize('status',['fetched','failed','blocked','unsupported','unavailable'])
def test_cached_terminal_fetch_does_not_trigger_another_wait_or_fetch(db,status):
    arrival(db,status=status)
    assert wait(db) is None


def test_unfinished_source_status_does_not_count_as_ready(db):
    arrival(db,status='queued')
    result=wait(db,clock=Clock(lambda seconds:arrival(db,status='failed') if seconds>=2 else None))
    assert result['elapsed_seconds']==2 and result['ready_documents']==1


@pytest.mark.parametrize('change,reason',[
    ('owner','source_owner_stopped'),('baseline','baseline_state_changed'),('extraction','new_extraction')])
def test_owner_stop_pause_and_new_extraction_end_wait(db,change,reason):
    def changed(seconds):
        if seconds!=2:return
        if change=='baseline':db.execute('INSERT INTO bulk_baseline_runs VALUES(?,?,?,?,?)',('old','paused',None,STAMP.isoformat(),STAMP.isoformat()))
        elif change=='extraction':db.execute("UPDATE state SET value=? WHERE key='collector_corpus_snapshot'",(json.dumps({'status':'complete','extracted_at':'changed'}),))
    clock=Clock(changed)
    owner=lambda _:None if change=='owner' and clock.elapsed>=2 else os.getpid()
    result=wait(db,clock=clock,owner=owner)
    assert result['reason']==reason and result['elapsed_seconds']==2


def test_only_new_inputs_wait_and_missing_timestamps_do_not_wait(db):
    s=freeze_item(item())
    assert wait(db,prior={s['document_id']:s['input_hash']}) is None
    assert wait(db,extraction={}) is None


@pytest.mark.parametrize('change',['disabled','paused','other_cycle','dead','engine_error'])
def test_actual_deep_owner_is_required(db,change):
    assert deep_owner(db)==os.getpid()
    if change=='disabled':db.execute('UPDATE risk_schedule SET enabled=0')
    elif change=='paused':db.execute('UPDATE rsi_cycles SET pause_requested=1')
    elif change=='other_cycle':db.execute("UPDATE risk_schedule SET cycle_id='other'")
    elif change=='engine_error':db.execute("UPDATE rsi_cycles SET error='[engine:rate_limit]'")
    else:
        db.execute('UPDATE risk_schedule SET launched_pid=NULL');db.execute('UPDATE rsi_cycles SET owner_pid=NULL')
    assert deep_owner(db) is None


@pytest.mark.parametrize('stop',['none','pause','models'])
def test_dispatch_rereads_sources_and_all_admission_gates_after_wait(db,stop):
    db.execute('INSERT INTO bulk_baseline_runs VALUES(?,?,?,?,?)',('old','requires_review',None,STAMP.isoformat(),STAMP.isoformat()))
    selections=[];calls=[];progress=[]
    def select(connection):
        from source_enrichment import attach_sources
        value=attach_sources(connection,[item()]);selections.append(value);return value
    def change(seconds):
        if seconds==2:
            arrival(db)
            if stop=='pause':db.execute("UPDATE bulk_baseline_runs SET status='paused'")
    clock=Clock(change)
    def bounded(*args,**kwargs):return wait_for_sources(*args,**kwargs,clock=clock,sleep=clock.sleep,owner=lambda _:os.getpid())
    def request(path,settings):
        calls.append((path,settings));return {'run':{'id':'next','status':'running','metrics':{}}}
    with patch('scheduled_collection.all_corpus_items',side_effect=select),patch('baseline_source_window.wait_for_sources',side_effect=bounded):
        result=dispatch(db,request,STAMP,check_models=lambda:{'ready':stop!='models'},progress=progress.append)
    assert progress[0]['stage']=='waiting_for_sources'
    assert result['source_coalescing']['elapsed_seconds']==2
    if stop=='none':
        assert result['stage']=='baseline_started' and len(calls)==1
        assert calls[0][1]['carry_forward_run']=='old'
        assert selections[-1][0]['source_context']['status']=='fetched'
        assert freeze_item(selections[-1][0])['input_hash']!=freeze_item(selections[0][0])['input_hash']
    else:
        assert not calls
        assert result['stage']==('baseline_attention_required' if stop=='pause' else 'model_access_required')
