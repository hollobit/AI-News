"""Process heartbeat, queue progress and explicit wait reasons; never a requeue policy."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time


def write_state(path, value):
    path = Path(path)
    temporary = path.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False))
    temporary.replace(path)


def queues(db_path, now=None):
    now = time.time() if now is None else now
    output = {}
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name,table in [('paper_metadata','arxiv_metadata_jobs'),('paper_analysis','arxiv_paper_analyses'),
                           ('article_explanations','article_explanations'),('wiki','wiki_topics')]:
            if table not in tables:
                output[name]={'state':'initializing'}
                continue
            columns={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            counts=dict(db.execute(f'SELECT status,COUNT(*) FROM {table} GROUP BY status')) if 'status' in columns else {}
            active=sum(counts.get(k,0) for k in ('running','reading','analyzing','reviewing'))
            queued=counts.get('queued',0)
            if name=='wiki' and 'requested' in columns:
                queued=db.execute('SELECT COUNT(*) FROM wiki_topics WHERE requested=1').fetchone()[0]
            stamp=next((key for key in ('updated_at','created_at','fetched_at') if key in columns),None)
            latest=db.execute(f'SELECT MAX({stamp}) FROM {table}').fetchone()[0] if stamp else None
            output[name]={'state':'working' if active else 'queued' if queued else 'idle',
                'counts':counts,'queued':queued,'last_progress_at':latest}
            if not active and counts.get('paused'):
                output[name].update(state='paused',wait_reason='job_paused')
        if 'arxiv_api_cooldown' in tables:
            retry=db.execute('SELECT MAX(retry_at) FROM arxiv_api_cooldown').fetchone()[0]
            if retry and retry>now:
                output['paper_metadata']['providers']={'arxiv':{'state':'cooldown','retry_at':retry}}
        if 'paper_pipeline_state' in tables:
            row=db.execute('SELECT enabled,next_at,failures FROM paper_pipeline_state WHERE id=1').fetchone()
            if row:
                enabled,next_at,failures=row
                output['paper_pipeline']={'state':'waiting' if enabled else 'paused',
                    'wait_reason':'scheduled' if enabled else 'pipeline_disabled','next_at':next_at,'failures':failures}
    return output


class WorkerHealth:
    def __init__(self, db, state, tasks):
        self.db,self.state=str(db),state
        self.stop=threading.Event();self.lock=threading.Lock()
        self.value={'pid':os.getpid(),'tasks':tasks,'ready':True,'started_at':time.time(),
                    'loop_at':time.time(),'phase':'idle'}
        self._write()
        self.thread=threading.Thread(target=self._loop,daemon=True,name='worker-heartbeat')
        self.thread.start()

    def tick(self, phase='idle'):
        with self.lock:self.value.update(loop_at=time.time(),phase=phase)

    def _write(self):
        with self.lock:value=dict(self.value)
        value['heartbeat_at']=time.time()
        try:value['queues']=queues(self.db)
        except (sqlite3.Error,OSError) as error:
            value.update(queue_state='unavailable',queue_error_type=type(error).__name__)
        write_state(self.state,value)

    def _loop(self):
        while not self.stop.wait(2):
            try:self._write()
            except OSError:pass

    def close(self):
        self.tick('draining');self.stop.set();self.thread.join(timeout=2)
        self._write()
