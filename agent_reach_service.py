"""Bounded background public-source reads with durable, inspectable results."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
import sqlite3
import threading
import uuid
from urllib.parse import urlsplit
from agent_reach_runtime import invoke
from source_enrichment import SourceService


def now():return datetime.now(timezone.utc).isoformat()


class AgentReachService:
    def __init__(self,path):
        self.path=str(path);self.lock=threading.Lock();self.closed=False
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='agent-reach')
        self.active={};self.info=None
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS agent_reach_jobs(id TEXT PRIMARY KEY,url TEXT,mode TEXT,
                status TEXT,result_json TEXT,error TEXT,created_at TEXT,updated_at TEXT,owner_pid INTEGER)''')
            from strategic_jobs import alive
            for row in db.execute("SELECT id,owner_pid FROM agent_reach_jobs WHERE status IN ('queued','running')"):
                if not alive(row['owner_pid']):db.execute("UPDATE agent_reach_jobs SET status='interrupted',error='읽기가 중단되었습니다. 다시 읽어 주세요.' WHERE id=?",(row['id'],))
        self.pool.submit(self._status)

    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db

    def _status(self):self.info=invoke({'action':'status'},timeout=15)

    def status(self):
        with self.db() as db:
            rows=db.execute('SELECT id,url,mode,status,error,created_at,updated_at FROM agent_reach_jobs ORDER BY created_at DESC LIMIT 20').fetchall()
        return dict(self.info or {'installed':None,'channels':[]},jobs=[dict(r) for r in rows])

    def submit(self,url,mode='auto'):
        if not isinstance(url,str) or len(url)>2048 or not isinstance(mode,str) or mode not in {'auto','rss'}:raise ValueError('URL과 읽기 형식을 확인해 주세요.')
        p=urlsplit(url)
        if p.scheme not in {'https','http'} or not p.hostname or p.username is not None or p.password is not None or any(ord(c)<32 for c in url):raise ValueError('공개 HTTP(S) 주소를 입력해 주세요.')
        with self.lock:
            if self.closed:raise ValueError('수집 서비스를 종료하고 있습니다.')
            if url in self.active:return {'id':self.active[url],'status':'queued','reused':True}
            if len(self.active)>=8:raise ValueError('읽기 대기열이 가득 찼습니다.')
            identity=uuid.uuid4().hex
            import os
            with self.db() as db:
                db.execute('INSERT INTO agent_reach_jobs VALUES (?,?,?,?,?,?,?,?,?)',(identity,url,mode,'queued','{}','',now(),now(),os.getpid()))
                db.execute("DELETE FROM agent_reach_jobs WHERE status NOT IN ('queued','running') AND id NOT IN (SELECT id FROM agent_reach_jobs ORDER BY created_at DESC LIMIT 100)")
            self.active[url]=identity;self.pool.submit(self._read,identity,url,mode)
        return {'id':identity,'status':'queued'}

    def _read(self,identity,url,mode):
        source=None
        try:
            with self.db() as db:db.execute("UPDATE agent_reach_jobs SET status='running',updated_at=? WHERE id=?",(now(),identity))
            source=SourceService(self.path,fetcher=lambda value:invoke({'action':'read','url':value,'mode':mode}))
            result=source.fetch(url,refresh=True)
            status='complete' if result.get('last_attempt_status',result['status'])=='fetched' else 'failed'
            with self.db() as db:db.execute('UPDATE agent_reach_jobs SET status=?,result_json=?,error=?,updated_at=? WHERE id=?',
                (status,json.dumps(result,ensure_ascii=False),result.get('error',''),now(),identity))
        except Exception:
            with self.db() as db:db.execute("UPDATE agent_reach_jobs SET status='failed',error='읽기 결과를 저장하지 못했습니다.',updated_at=? WHERE id=?",(now(),identity))
        finally:
            if source:source.close()
            with self.lock:self.active.pop(url,None)

    def get(self,identity):
        with self.db() as db:row=db.execute('SELECT * FROM agent_reach_jobs WHERE id=?',(identity,)).fetchone()
        if not row:return None
        value=dict(row);value['result']=json.loads(value.pop('result_json'));value.pop('owner_pid',None);return value

    def close(self):
        self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True)
