import json
from datetime import datetime,timezone
import pytest
from corpus_completion import connect
from scheduled_risks import dispatch,init


@pytest.fixture
def path(tmp_path):
    p=tmp_path/'news.db'
    with connect(p) as db:
        init(db)
        db.execute('CREATE TABLE rsi_cycles(id TEXT,status TEXT,pause_requested INTEGER,owner_pid INTEGER,created_at TEXT,error TEXT)')
        db.execute("INSERT INTO rsi_cycles VALUES('cycle','paused',1,NULL,'now','[engine:circuit_open]')")
        db.execute('CREATE TABLE state(key TEXT,value TEXT)')
        db.executemany('INSERT INTO state VALUES(?,?)',[
            ('collector_last_success',datetime.fromtimestamp(1000,timezone.utc).isoformat()),
            ('collector_corpus_snapshot',json.dumps({'status':'complete'}))])
        db.execute('CREATE TABLE corpus_completion_documents(cycle_id TEXT,status TEXT)')
    return p


def run(path,**kwargs):
    kwargs.setdefault('check_models',lambda:{'ready':True})
    return dispatch(path,stamp=1000,is_alive=lambda pid:bool(pid),launcher=lambda *_:42,check_engine=lambda:True,**kwargs)


def test_explicit_resume_then_live_launch_prevents_duplicate(path):
    assert run(path,enable=True)['stage']=='user_paused'
    assert run(path,resume=True)['stage']=='started'
    assert run(path)['stage']=='running'
    with connect(path) as db:
        assert db.execute('select pause_requested from rsi_cycles').fetchone()[0]==0
        assert db.execute("select count(*) from risk_schedule_events where stage='explicit_resume'").fetchone()[0]==1


def test_quota_is_not_automatically_retried(path):
    with connect(path) as db:
        db.execute("UPDATE rsi_cycles SET pause_requested=0,error='[engine:rate_limit]'")
    assert run(path,enable=True)['stage']=='attention_required'


def test_recovery_budget_and_backoff(path):
    with connect(path) as db:db.execute('UPDATE rsi_cycles SET pause_requested=0')
    assert run(path,enable=True)['stage']=='started'
    with connect(path) as db:db.execute('UPDATE risk_schedule SET launched_pid=NULL')
    assert run(path)['stage']=='recovery_backoff'
    with connect(path) as db:db.execute('UPDATE risk_schedule SET stagnant=3,next_attempt_at=0')
    assert run(path)['stage']=='recovery_limit'


def test_user_pause_during_probe_wins(path):
    def pause():
        with connect(path) as db:db.execute('UPDATE rsi_cycles SET pause_requested=1')
        return True
    result=dispatch(path,enable=True,resume=True,stamp=1000,is_alive=lambda _:False,
                    check_models=lambda:{'ready':True},check_engine=pause,launcher=lambda *_:pytest.fail('must not launch'))
    assert result['stage']=='admission_changed'


def test_latest_model_access_blocks_launch(path):
    assert run(path,enable=True,resume=True,check_models=lambda:{'ready':False})['stage']=='model_access_required'
    with connect(path) as db:
        assert db.execute('select launched_pid from risk_schedule').fetchone()[0] is None
