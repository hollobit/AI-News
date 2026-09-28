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
