"""One detached worker per archive for paper, wiki and article durable queues."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
from background_jobs import paths
from recursive_improvement import owner_alive


class SavedObservatory:
    def __init__(self, db):
        self.directory = Path(db + '.observatory')

    def request(self, window=14):
        for name in (f'{window}-default-v2.json', f'{window}-v2.json'):
            try:
                saved = json.loads((self.directory / name).read_text())
                if saved.get('window') == window and not saved.get('expanded') and len(saved.get('data', {}).get('days', [])) == window:
                    return saved['data']
            except (OSError, ValueError, TypeError):
                pass
        return {'status': 'preparing'}


def claim(db, table, id_column, owner_column, statuses):
    """Atomic takeover only for ownerless/dead work, preserving prior inputs."""
    with sqlite3.connect(db, timeout=15) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN IMMEDIATE')
        placeholders = ','.join('?' for _ in statuses)
        rows = connection.execute(f'SELECT * FROM {table} WHERE status IN ({placeholders}) ORDER BY rowid', statuses)
        row = next((r for r in rows if not owner_alive(r[owner_column])), None)
        if row:
            connection.execute(f'UPDATE {table} SET {owner_column}=? WHERE {id_column}=?', (os.getpid(), row[id_column]))
            return dict(row)


def initialize_services(db):
    """Finish schema/recovery setup before any queue starts writing results."""
    from paper_analysis import PaperAnalysisService
    from arxiv_papers import PaperService
    from paper_pipeline import PaperPipeline
    from knowledge_wiki import KnowledgeWiki
    from article_explanations import ArticleExplanations
    services=[]
    stage='paper_analysis'
    try:
        analysis=PaperAnalysisService(db,start_worker=False);services.append(analysis)
        stage='paper_metadata'
        metadata=PaperService(db,start_worker=False);services.append(metadata)
        stage='paper_pipeline'
        pipeline=PaperPipeline(db,metadata,analysis,autostart=False);services.append(pipeline)
        stage='wiki'
        wiki=KnowledgeWiki(db,start_worker=False);services.append(wiki)
        stage='article_explanations'
        articles=ArticleExplanations(db,SavedObservatory(db),start_worker=False);services.append(articles)
        return analysis,metadata,pipeline,wiki,articles
    except Exception as error:
        error.worker_stage=stage
        for service in reversed(services):service.close()
        raise


def failure_details(error):
    # Exception messages can contain provider input. These fields are controlled
    # component names and SQLite's own error identifiers, never request data.
    result=dict(status='failed',error_type=type(error).__name__,stage=getattr(error,'worker_stage','run'))
    if isinstance(error,sqlite3.Error):result['sqlite_error']=getattr(error,'sqlite_errorname','UNKNOWN')
    return result


def run(db):
    lock, state = paths(db)
    with lock.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        analysis,metadata,pipeline,wiki,articles=initialize_services(db)
        pool = __import__('concurrent.futures', fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor(2, thread_name_prefix='durable-background')
        from worker_health import WorkerHealth
        health = WorkerHealth(db, state, ['paper_metadata','paper_analysis','paper_pipeline','wiki','article_explanations'])
        pending = {}
        wiki_request = None
        from wiki_wakeup import InputWatch, notify
        wiki_inputs = InputWatch()
        try:
            if stop.is_set():return
            # Establish the input counter before the initial wiki sweep so an
            # update during that sweep remains observable by the same worker.
            with sqlite3.connect(db, timeout=15) as connection:
                wiki_inputs.poll(connection, enabled=wiki.enabled)
            analysis.thread.start()
            pipeline.autostart=True
            pipeline.thread.start();pipeline.provider_thread.start()
            wiki.thread=threading.Thread(target=wiki._loop,daemon=True,name='knowledge-wiki')
            wiki.thread.start()
            while not stop.wait(2):
                health.tick("claiming")
                for kind, future in list(pending.items()):
                    if future.done():
                        try: future.result()
                        except Exception: pass  # Services preserve controlled failure state in the DB.
                        pending.pop(kind)
                if 'metadata' not in pending:
                    row = claim(db, 'arxiv_metadata_jobs', 'id', 'owner_pid', ('queued', 'running'))
                    if row:
                        pending['metadata'] = pool.submit(metadata._run, row['id'], json.loads(row['ids_json']))
                if 'article' not in pending:
                    row = claim(db, 'article_explanations', 'id', 'owner', ('queued','reading','analyzing','reviewing'))
                    if row:
                        pending['article'] = pool.submit(articles._run, row['url'])
                with sqlite3.connect(db, timeout=15) as connection:
                    requested = tuple(connection.execute('SELECT id,requested FROM wiki_topics WHERE requested=1 ORDER BY id'))
                    notify(connection, wiki_inputs, wiki)
                if requested != wiki_request:
                    wiki_request = requested
                    if requested:
                        wiki.wake.set()
                health.tick("working" if pending else "idle", wiki_source_updates=wiki_inputs.status())
        finally:
            health.tick('draining')
            wiki.close(); analysis.close(); pipeline.close()
            # Finish/checkpoint existing work before releasing the ownership lock.
            pool.shutdown(wait=True)
            if wiki.thread: wiki.thread.join()
            if analysis.thread.is_alive(): analysis.thread.join()
            if pipeline.thread.is_alive(): pipeline.thread.join()
            if pipeline.provider_thread.is_alive(): pipeline.provider_thread.join()
            metadata.close(); articles.close()
            health.close()
            state.unlink(missing_ok=True)


if __name__ == '__main__':
    from app import load_local_env
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    args = parser.parse_args()
    try:
        run(str(Path(args.db).resolve()))
    except Exception as error:
        details=failure_details(error)
        from worker_health import write_state
        import time
        try:write_state(Path(str(paths(args.db)[1])+'.failure.json'),dict(details,at=time.time(),pid=os.getpid()))
        except OSError:pass
        print(json.dumps(details), flush=True)
        raise SystemExit(1)
