import os
from unittest.mock import patch
import pytest
from baseline_jobs import BaselineJobs


def test_external_pause_keeps_owner_until_worker_checkpoints_and_close_does_not_signal(tmp_path,monkeypatch):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    jobs=BaselineJobs(tmp_path/'news.db',lambda:[])
    with jobs.reader.db() as db:
        db.execute("INSERT INTO bulk_baseline_runs VALUES ('run','running','{}','now','now',?,'')",(os.getpid(),))
    with patch('baseline_jobs.subprocess.Popen') as launch:
        jobs.pause('run')
        with jobs.reader.db() as db:
            assert tuple(db.execute("SELECT status,owner_pid FROM bulk_baseline_runs").fetchone())==('finishing',os.getpid())
            assert db.execute("SELECT stage FROM bulk_baseline_events").fetchone()[0]=='user_pause_requested'
        jobs.close()
        launch.assert_not_called()


def test_live_owner_rejects_duplicate_worker_before_subprocess(tmp_path,monkeypatch):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    jobs=BaselineJobs(tmp_path/'news.db',lambda:[])
    with jobs.reader.db() as db:
        db.execute("INSERT INTO bulk_baseline_runs VALUES ('run','running','{}','now','now',?,'')",(os.getpid(),))
    with patch('baseline_jobs.subprocess.Popen') as launch:
        with pytest.raises(RuntimeError,match='다른 프로세스'):jobs.start()
        launch.assert_not_called()
    jobs.close()


def test_worker_exit_is_reaped_after_ready_response(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    import threading
    from pathlib import Path

    jobs = BaselineJobs(tmp_path / 'news.db', lambda: [])
    reaped = threading.Event()
    launch = subprocess.Popen

    def start_child(command, **kwargs):
        ready = Path(command[command.index('--ready') + 1])
        child = launch([sys.executable, '-c', 'import time; time.sleep(.1)'], **kwargs)
        wait = child.wait

        def reap():
            result = wait()
            reaped.set()
            return result

        child.wait = reap
        ready.write_text(json.dumps({'run': {'id': 'finished'}}))
        return child

    monkeypatch.setattr(jobs.reader, '_available', lambda: None)
    monkeypatch.setattr(jobs, 'get', lambda identity: {'id': identity})
    monkeypatch.setattr('baseline_jobs.subprocess.Popen', start_child)
    assert jobs.start() == {'id': 'finished'}
    assert reaped.wait(5)
    jobs.close()


def test_baseline_connections_close_and_preserve_transactions(tmp_path):
    import sqlite3

    jobs = BaselineJobs(tmp_path / 'news.db', lambda: [])
    with jobs.reader.db() as connection:
        connection.execute('CREATE TABLE lifecycle(value INTEGER)')
        connection.execute('INSERT INTO lifecycle VALUES (1)')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')
    with pytest.raises(ValueError):
        with jobs.reader.db() as failed:
            failed.execute('INSERT INTO lifecycle VALUES (2)')
            raise ValueError('rollback')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        failed.execute('SELECT 1')
    with jobs.reader.db() as connection:
        assert [r[0] for r in connection.execute('SELECT value FROM lifecycle')] == [1]
    jobs.close()
