import sqlite3
from unittest.mock import patch
import pytest
from automation_runtime import server_lease, interrupted_runs, recover


def test_only_interrupted_active_work_recovers_and_rsi_owns_child(tmp_path):
    path=tmp_path/'news.db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE rsi_cycles(id TEXT,status TEXT,pause_requested INTEGER,owner_pid INTEGER,created_at TEXT);
        CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,created_at TEXT);
        CREATE TABLE rsi_rounds(workflow_run_id TEXT);
        CREATE TABLE bulk_baseline_runs(id TEXT,status TEXT,owner_pid INTEGER,created_at TEXT);''')
        db.execute("INSERT INTO rsi_cycles VALUES('cycle','running',0,999999999,'today')")
        db.execute("INSERT INTO strategic_workflow_runs VALUES('child','running','today')")
        db.execute("INSERT INTO rsi_rounds VALUES('child')")
        db.execute("INSERT INTO bulk_baseline_runs VALUES('base','running',999999999,'today')")
    with patch('automation_runtime._alive',return_value=False):
        assert interrupted_runs(path)=={'baseline':'base','improvement':'cycle'}
        with sqlite3.connect(path) as db:
            db.execute("UPDATE rsi_cycles SET status='finishing',pause_requested=1")
            db.execute("UPDATE bulk_baseline_runs SET status='paused'")
        assert interrupted_runs(path)=={}


def test_live_owner_and_duplicate_server_are_not_interrupted(tmp_path):
    path=tmp_path/'news.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE bulk_baseline_runs(id TEXT,status TEXT,owner_pid INTEGER,created_at TEXT)')
        db.execute("INSERT INTO bulk_baseline_runs VALUES('base','running',123,'today')")
    with patch('automation_runtime._alive',return_value=True):assert interrupted_runs(path)=={}
    with server_lease(path):
        with pytest.raises(RuntimeError):
            with server_lease(path):pass
    with server_lease(path):pass


def test_recovery_reuses_existing_id_once_and_reports_failure():
    class Service:
        def __init__(self):self.calls=[]
        def resume(self,identity):self.calls.append(identity)
    service=Service()
    recover({'baseline':'same-run'},{'baseline':service})
    assert service.calls==['same-run']


def test_external_workflow_owner_is_not_resumed_by_web_server(tmp_path):
    import json
    import os
    path = tmp_path / 'news.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,created_at TEXT,request_json TEXT)')
        db.execute('INSERT INTO strategic_workflow_runs VALUES(?,?,?,?)',
                   ('external', 'running', 'today', json.dumps({'owner_pid': os.getpid()})))
    assert interrupted_runs(path) == {}


def test_managed_cycle_is_not_adopted_or_marked_user_paused_by_web(tmp_path):
    from recursive_improvement import RecursiveImprovementService
    path = tmp_path/'news.sqlite'
    service = RecursiveImprovementService(path, None, lambda *_: [])
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE risk_schedule(enabled INTEGER,cycle_id TEXT)")
        db.execute("INSERT INTO risk_schedule VALUES(1,'managed')")
        db.execute("INSERT INTO rsi_cycles(id,status,settings_json,created_at,updated_at) VALUES('managed','waiting','{}','2026-09-30','2026-09-30')")
    assert interrupted_runs(path) == {}
    RecursiveImprovementService(path, None, lambda *_: [])
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status,pause_requested FROM rsi_cycles WHERE id='managed'").fetchone() == ('waiting',0)
