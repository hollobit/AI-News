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


def test_meaningful_messages_and_mixed_batches_only_join_fast_sources(db):
    rich=item('rich','현장 조건과 측정 결과를 확인하기 위한 새로운 실험 및 평가 방법을 발표했습니다. '*5)
    assert not headline_only(freeze_item(rich))
    result=wait(db,[rich])
    assert result['mode']=='short' and result['elapsed_seconds']==result['maximum_seconds']==5
    clock=Clock(lambda seconds: [arrival(db,name) for name in ('a','rich')] if seconds>=3 else None)
    result=wait(db,[item(),rich],clock)
    assert result['mode']=='short' and result['reason']=='sources_ready'
    assert result['elapsed_seconds']==3 and result['ready_documents']==2
    assert wait(db,[item(str(i)) for i in range(65)]) is None


@pytest.mark.parametrize('age,expected',[(0,5),(3,2),(5,None),(6,None)])
def test_short_window_deadline_is_bound_to_original_extraction(db,age,expected):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    result=wait(db,[rich],now=STAMP+timedelta(seconds=age))
    if expected is None:assert result is None
    else:assert result['elapsed_seconds']==expected and result['reason']=='deadline'


def test_short_window_keeps_terminal_sources_and_reports_arrival_at_deadline(db):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    arrival(db,'a',status='blocked')
    progress=[]
    result=wait(db,[item(),rich],Clock(lambda seconds:arrival(db,'rich') if seconds>=5 else None),progress=progress.append)
    assert result['reason']=='sources_ready' and result['elapsed_seconds']==5
    assert result['documents']==result['ready_documents']==1
    assert progress[0]['remaining_seconds']==5 and progress[0]['mode']=='short'
    assert wait(db,[item(),rich]) is None


def test_short_window_requires_a_live_owner_and_never_fetches_sources(db):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    with patch('source_enrichment.SourceService.fetch') as fetch,patch('semantic.run_structured') as model:
        assert wait(db,[rich],owner=lambda _:None) is None
    fetch.assert_not_called();model.assert_not_called()


def test_long_headline_window_does_not_wait_for_a_substantive_minority(db):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    names=[str(i) for i in range(4)]
    clock=Clock(lambda seconds:[arrival(db,name) for name in names] if seconds>=2 else None)
    result=wait(db,[item(name) for name in names]+[rich],clock)
    assert result['mode']=='headline' and result['maximum_seconds']==45
    assert result['documents']==result['ready_documents']==4
    assert result['elapsed_seconds']==2 and result['reason']=='sources_ready'


@pytest.mark.parametrize('terminal',['fetched','failed','blocked','unsupported','unavailable'])
def test_ready_headline_cannot_shorten_wait_for_other_headline_sources(db,terminal):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    names=[str(i) for i in range(4)]
    arrival(db,'0',status=terminal)
    clock=Clock(lambda seconds:[arrival(db,name) for name in names[1:]] if seconds>=12 else None)
    result=wait(db,[item(name) for name in names]+[rich],clock)
    assert result['mode']=='headline' and result['maximum_seconds']==45
    assert result['documents']==result['ready_documents']==3
    assert result['elapsed_seconds']==12 and result['reason']=='sources_ready'


def test_all_headline_sources_ready_do_not_wait_for_substantive_minority(db):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    names=[str(i) for i in range(4)]
    for name in names:arrival(db,name)
    assert wait(db,[item(name) for name in names]+[rich]) is None


def test_dispatch_snapshot_preparation_spends_the_same_short_window(db):
    rich=item('rich','작동 조건과 측정 결과를 포함한 실제 본문. '*10)
    clock=Clock();progress=[]
    def bounded(*args,**kwargs):
        return wait_for_sources(*args,**kwargs,clock=clock,sleep=clock.sleep,owner=lambda _:os.getpid())
    request=lambda *_:{'run':{'id':'next','status':'running','metrics':{}}}
    with patch('scheduled_collection.all_corpus_items',return_value=[rich]), \
            patch('scheduled_collection.monotonic',side_effect=[0,4,5]), \
            patch('baseline_source_window.wait_for_sources',side_effect=bounded):
        result=dispatch(db,request,STAMP,check_models=lambda:{'ready':True},progress=progress.append)
    assert result['source_coalescing']['elapsed_seconds']==1
    assert progress[0]['source_coalescing']['remaining_seconds']==1
    assert progress[0]['source_coalescing']['deadline']==(STAMP+timedelta(seconds=5)).isoformat()
    assert result['checked_at']==(STAMP+timedelta(seconds=5)).isoformat()


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
        assert calls[0][1]['source_coalescing']==result['source_coalescing']
        assert calls[0][1]['source_coalescing']['extracted_at']==STAMP.isoformat()
        assert selections[-1][0]['source_context']['status']=='fetched'
        assert freeze_item(selections[-1][0])['input_hash']!=freeze_item(selections[0][0])['input_hash']
    else:
        assert not calls
        assert result['stage']==('baseline_attention_required' if stop=='pause' else 'model_access_required')
