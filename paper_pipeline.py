"""Persistent, bounded collection-to-reviewed-analysis scheduling."""
import json,sqlite3,threading,time
from datetime import datetime,timezone

class PaperPipeline:
    def __init__(self,path,metadata,analysis,autostart=True,engine_ready=None):
        self.path=str(path);self.metadata=metadata;self.analysis=analysis;self.stop=threading.Event()
        from paper_recovery import engine_ready as shared_engine_ready
        self.engine_ready=engine_ready or shared_engine_ready
        self.autostart=autostart
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS paper_pipeline_state(id INTEGER PRIMARY KEY,enabled INTEGER,next_at REAL,failures INTEGER,last_job TEXT,error TEXT);
            INSERT OR IGNORE INTO paper_pipeline_state VALUES(1,1,0,0,'','');
            CREATE TABLE IF NOT EXISTS paper_pipeline_attempts(paper_id TEXT,kind TEXT,attempts INTEGER,at REAL,PRIMARY KEY(paper_id,kind));''')
            from paper_recovery import setup
            setup(db)
            from context_paper_delta import install
            install(db)
        from paper_pipeline_inputs import PaperInputIndex
        self.input_index=PaperInputIndex()
        from paper_metadata_sources import AlternateMetadata
        self.alternates=AlternateMetadata(path)
        self.thread=threading.Thread(target=self._loop,daemon=True,name='paper-pipeline')
        self.provider_thread=threading.Thread(target=self._provider_loop,daemon=True,name='paper-providers')
        if autostart:
            self.thread.start();self.provider_thread.start()
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db
    def status(self):
        with self.db() as db:r=dict(db.execute('SELECT * FROM paper_pipeline_state WHERE id=1').fetchone())
        r['next_retry_at']=datetime.fromtimestamp(r['next_at'],timezone.utc).isoformat() if r['next_at']>time.time() else None
        r['official_api_paused']=r['failures']>=3
        r['providers']=self.alternates.status() if hasattr(self,'alternates') else []
        return r
    def configure(self,enabled):
        if type(enabled) is not bool:raise ValueError('enabled는 boolean이어야 합니다.')
        with self.db() as db:
            db.execute('UPDATE paper_pipeline_state SET enabled=? WHERE id=1',(int(enabled),))
            # Admission history is monotonic; resuming must not replenish retries.
        return self.status()
    def _loop(self):
        while not self.stop.is_set():
            try:self.tick()
            except Exception as e:
                try:
                    with self.db() as db:db.execute('UPDATE paper_pipeline_state SET error=?,next_at=? WHERE id=1',(str(e)[:400],time.time()+60))
                except sqlite3.OperationalError:
                    # Error reporting must not kill the scheduler while another
                    # writer holds the database; the next tick retries normally.
                    pass
            self.stop.wait(2)
    def _provider_loop(self):
        while not self.stop.is_set():
            try:
                if self.status()['enabled']:self.alternates.tick()
            except sqlite3.Error:pass
            self.stop.wait(10)
    def _record(self,ids,kind):
        with self.db() as db:
            db.executemany('INSERT INTO paper_pipeline_attempts VALUES(?,?,1,?) ON CONFLICT(paper_id,kind) DO UPDATE SET attempts=attempts+1,at=excluded.at',[(i,kind,time.time()) for i in ids])
    def tick(self):
        state=self.status()
        if not state['enabled']:return
        stamp=time.time()
        with self.db() as db:
            revision=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='state'").fetchone() else None
        revision=revision[0] if revision else None
        if stamp-getattr(self,'last_discovery',0)>=60 or revision!=getattr(self,'discovery_revision',None):
            from arxiv_papers import discover_papers
            with self.db() as db:discover_papers(db)
            self.last_discovery=stamp
            self.discovery_revision=revision
        if not self.autostart:self.alternates.tick()
        # Only fetch-backed papers enter analysis. Never use discovery titles as abstracts.
        if self.analysis.enabled and self.engine_ready():
            with self.db() as db:
                pending=db.execute("SELECT count(*) FROM arxiv_paper_analyses WHERE status IN ('queued','running')").fetchone()[0]
                columns={r[1] for r in db.execute('PRAGMA table_info(arxiv_paper_analyses)')}
                fields=','.join(k for k in ('paper_id','input_hash','status','error','updated_at') if k in columns)
                analyses={r['paper_id']:dict(r) for r in db.execute('SELECT '+fields+' FROM arxiv_paper_analyses')}
                attempts={r['paper_id']:dict(r) for r in db.execute("SELECT * FROM paper_pipeline_attempts WHERE kind='analysis'")}
                capacity=min(16,max(4,getattr(self.analysis,'workers',1)*2))
                slots=min(10,capacity-pending);candidates=[]
                for item in self.input_index.current(db) if slots>0 else []:
                    if item.metadata_status!='fetched':continue
                    existing=analyses.get(item.paper_id);attempt=attempts.get(item.paper_id,{})
                    if existing and existing['status'] in ('queued','running','paused'):continue
                    changed=existing and existing['input_hash']!=item.input_hash
                    if existing and existing['status']!='failed' and not changed:continue
                    from paper_recovery import eligible, reserve
                    from engine_errors import infrastructure_error
                    infrastructure=existing and not changed and existing['status']=='failed' and infrastructure_error(existing.get('error'))
                    if infrastructure:
                        if not eligible(db,existing,stamp):continue
                        if not reserve(db,existing,stamp):continue
                    elif not changed and (attempt.get('attempts',0)>=2 or attempt.get('at',0)>stamp-3600):continue
                    candidates.append(item.paper_id)
                    if len(candidates)>=slots:break
            if candidates:
                result=self.analysis.submit(candidates,limit=slots);self._record(result['queued'],'analysis')
        if state['failures']>=3:return
        if self.metadata.active:return
        with self.db() as db:
            if db.execute("SELECT 1 FROM arxiv_metadata_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():return
        if state['last_job']:
            with self.db() as db:job=db.execute('SELECT status,error FROM arxiv_metadata_jobs WHERE id=?',(state['last_job'],)).fetchone()
            if job and job['status'] in ('queued','running'):return
            if job and job['status'] in ('failed','interrupted','deferred'):
                failures=state['failures']+1
                with self.db() as db:
                    delay=max(stamp+3600,self.metadata.cooldown_until(db))
                    db.execute("UPDATE paper_pipeline_state SET failures=?,enabled=?,next_at=?,error=?,last_job='' WHERE id=1",(failures,1,delay,job['error'] or '논문 메타데이터 재시도 대기'))
                return
            with self.db() as db:db.execute("UPDATE paper_pipeline_state SET failures=0,last_job='',error='' WHERE id=1")
        if state['next_at']>stamp:return
        with self.db() as db:
            cooldown=self.metadata.cooldown_until(db)
            ids=[r[0] for r in db.execute("""SELECT p.paper_id FROM arxiv_papers p LEFT JOIN paper_pipeline_attempts x ON x.paper_id=p.paper_id AND x.kind='metadata'
                WHERE p.status!='fetched' AND COALESCE(x.attempts,0)<3 ORDER BY p.status='failed',p.paper_id DESC LIMIT 10""")]
        if cooldown>stamp:
            with self.db() as db:db.execute("UPDATE paper_pipeline_state SET next_at=?,error='arXiv 요청 제한으로 재시도 대기' WHERE id=1",(cooldown,))
            return
        if not ids:
            with self.db() as db:db.execute('UPDATE paper_pipeline_state SET next_at=? WHERE id=1',(stamp+300,))
            return
        result=self.metadata.refresh(ids,limit=10)
        if result.get('id'):self._record(result['paper_ids'],'metadata')
        with self.db() as db:db.execute('UPDATE paper_pipeline_state SET last_job=?,next_at=? WHERE id=1',(result.get('id',''),stamp+(30 if result.get('id') else 300)))
    def close(self):
        self.stop.set()
        if self.thread.is_alive():self.thread.join(timeout=2)
        if self.provider_thread.is_alive():self.provider_thread.join(timeout=2)
