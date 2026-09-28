import os
import signal
import sqlite3
import time
from unittest.mock import patch
from background_jobs import BackgroundJobs, worker_status
from paper_analysis import PaperAnalysisService
from knowledge_wiki import KnowledgeWiki
from arxiv_papers import PaperService
from article_explanations import ArticleExplanations


def test_detached_worker_survives_reader_close_and_server_monitor_restart(tmp_path, monkeypatch):
    monkeypatch.delenv('NEWS_EXTERNAL_ANALYSIS_ENABLED', raising=False)
    db=tmp_path/'news.db'
    # An empty archive ensures this process proof performs no network/model work.
    from app import connect
    with connect(db):pass
    manager=BackgroundJobs(db,monitor=False)
    pid=manager.status()['pid']
    try:
        assert pid != os.getpid()
        paper=PaperAnalysisService(db,enabled=False,start_worker=False)
        wiki=KnowledgeWiki(db,enabled=False,start_worker=False)
        metadata=PaperService(db,start_worker=False)
        articles=ArticleExplanations(db,None,start_worker=False)
        paper.close();wiki.close();metadata.close();articles.close();manager.close()
        os.kill(pid,0)
        with patch('background_jobs.subprocess.Popen') as launch:
            second=BackgroundJobs(db,monitor=False)
            assert second.status()['pid']==pid
            launch.assert_not_called()
            second.close()
        assert worker_status(db)['status']=='running'
    finally:
        os.kill(pid,signal.SIGTERM)
        deadline=time.monotonic()+10
        while worker_status(db)['status']!='stopped' and time.monotonic()<deadline:time.sleep(.05)
        assert worker_status(db)['status']=='stopped'


def test_read_facades_enqueue_ownerless_work_without_starting_threads(tmp_path, monkeypatch):
    monkeypatch.setenv('NEWS_EXTERNAL_ANALYSIS_ENABLED','1')
    from app import connect
    with connect(tmp_path/'news.db'):pass
    service=ArticleExplanations(tmp_path/'news.db',None,start_worker=False)
    result=service.submit('https://example.org/article')
    assert result['status']=='queued' and service.pool is None
    with service.db() as db:
        assert db.execute('SELECT owner FROM article_explanations').fetchone()[0] is None
    service.close()
