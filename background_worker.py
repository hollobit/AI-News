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
        from paper_analysis import PaperAnalysisService
        from arxiv_papers import PaperService
        from paper_pipeline import PaperPipeline
        from knowledge_wiki import KnowledgeWiki
        from article_explanations import ArticleExplanations
        analysis = PaperAnalysisService(db)
        metadata = PaperService(db, start_worker=False)
        pipeline = PaperPipeline(db, metadata, analysis)
        wiki = KnowledgeWiki(db)
        articles = ArticleExplanations(db, SavedObservatory(db), start_worker=False)
        pool = __import__('concurrent.futures', fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor(2, thread_name_prefix='durable-background')
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pid': os.getpid(), 'tasks': ['paper_metadata','paper_analysis','paper_pipeline','wiki','article_explanations']}))
        temporary.replace(state)
        pending = {}
        wiki_request = None
        try:
            while not stop.wait(2):
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
                if requested != wiki_request:
                    wiki_request = requested
                    wiki.wake.set()
        finally:
            wiki.close(); analysis.close(); pipeline.close()
            # Finish/checkpoint existing work before releasing the ownership lock.
            pool.shutdown(wait=True)
            if wiki.thread: wiki.thread.join()
            if analysis.thread.is_alive(): analysis.thread.join()
            if pipeline.thread.is_alive(): pipeline.thread.join()
            metadata.close(); articles.close()
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
        print(json.dumps({'status':'failed','error_type':type(error).__name__}), flush=True)
        raise SystemExit(1)
