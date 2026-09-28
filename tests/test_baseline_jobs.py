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
