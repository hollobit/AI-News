import sqlite3
import time
from pathlib import Path
from unittest.mock import patch
from worker_health import queues,WorkerHealth
from background_jobs import BackgroundJobs


def test_queue_progress_pause_and_provider_cooldown_are_separate(tmp_path):
    path=tmp_path/'news.db'
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE arxiv_metadata_jobs(status TEXT,created_at TEXT);
            INSERT INTO arxiv_metadata_jobs VALUES('running','2026-09-29');
            CREATE TABLE arxiv_paper_analyses(status TEXT,updated_at TEXT);
            INSERT INTO arxiv_paper_analyses VALUES('paused','2026-09-29');
            CREATE TABLE arxiv_api_cooldown(retry_at REAL);
            INSERT INTO arxiv_api_cooldown VALUES(200);
            CREATE TABLE paper_pipeline_state(id INTEGER,enabled INTEGER,next_at REAL,failures INTEGER);
            INSERT INTO paper_pipeline_state VALUES(1,0,300,3);""")
    result=queues(path,now=100)
    assert result['paper_metadata']['state']=='working'
    assert result['paper_metadata']['providers']['arxiv']['state']=='cooldown'
    assert result['paper_analysis']['state']=='paused'
    assert result['paper_pipeline']['wait_reason']=='pipeline_disabled'
    assert result['article_explanations']['state']=='initializing'


def test_monitor_failure_is_reported_without_requeueing(tmp_path):
    import threading
    manager=BackgroundJobs.__new__(BackgroundJobs)
    manager.db=str(tmp_path/'db');manager.monitor_file=tmp_path/'monitor.json'
    manager.monitor_state={'pid':1,'state':'running','failures':0}
    manager.stop=type('Immediate',(),{'wait':lambda self,seconds:False})()
    with patch.object(manager,'ensure',side_effect=RuntimeError('private provider text')) as ensure:
        manager._monitor()
    assert ensure.call_count==3
    assert manager.monitor_state['state']=='failed' and manager.monitor_state['failures']==3
    assert manager.monitor_state['error_type']=='RuntimeError'
    assert 'private provider text' not in manager.monitor_file.read_text()
