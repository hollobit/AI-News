"""Durable bounded background recovery, subscriptions and evidence discovery."""
import hashlib,json,os,sqlite3,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit
from agent_reach_runtime import invoke
from source_enrichment import SourceService
from source_store import init as init_store,error_kind,now
from link_groups import canonical_url


def public_url(url):
    if not isinstance(url,str) or len(url)>2048:raise ValueError('공개 URL을 확인해 주세요.')
    p=urlsplit(url)
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or any(ord(c)<32 for c in url):raise ValueError('공개 HTTP(S) URL이 필요합니다.')
    return canonical_url(url)


def external_rows(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='reach_observations'").fetchone():return []
    rows=db.execute('SELECT url,title,text,published_at,observed_at,subscription FROM reach_observations')
    output=[]
    for url,title,text,published,observed,sub in rows:
        date=published[:10] if published else observed[:10]
        output.append(dict(chat_id=-9900,message_id=int(hashlib.sha256(url.encode()).hexdigest()[:12],16),item_index=0,
            title=title or url,excerpt=text[:600],text=text,day=date,date_basis='article' if published else 'observed',topic='general',kind='article',
            source_url=url,url=url,channel='외부 관측 · '+sub,published_at=published or '',telegram_day='',source_origin='external_watch',origin='external_watch'))
    return output


class ReachPipeline:
    def __init__(self,path,autostart=True,reader=None,reviewer=True):
        self.path=str(path);self.invoke=reader or invoke;self.stop=threading.Event();self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='reach-pipeline');self.active=set();self.lock=threading.Lock();self.workflow=None;self.reviewer=reviewer
        with self.db() as db:
            from source_enrichment import init_sources
            init_sources(db)
            init_store(db)
            db.executescript('''
            CREATE TABLE IF NOT EXISTS reach_tasks(id TEXT PRIMARY KEY,task_key TEXT UNIQUE,kind TEXT,payload TEXT,status TEXT,attempts INTEGER,next_at REAL,result TEXT,error TEXT,owner INTEGER,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS reach_subscriptions(id TEXT PRIMARY KEY,url TEXT UNIQUE,label TEXT,keywords TEXT,mode TEXT,interval_seconds INTEGER,enabled INTEGER,next_at REAL,last_checked TEXT,last_success TEXT,error TEXT);
            CREATE TABLE IF NOT EXISTS reach_observations(url TEXT PRIMARY KEY,title TEXT,text TEXT,published_at TEXT,observed_at TEXT,subscription TEXT);
            ''')
            from strategic_jobs import alive
            for row in db.execute("SELECT id,owner FROM reach_tasks WHERE status='running'"):
                if not alive(row['owner']):db.execute("UPDATE reach_tasks SET status='queued',owner=NULL WHERE id=?",(row['id'],))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_excerpts'").fetchone():
                for url,payload,updated in db.execute('SELECT canonical_url,result_json,updated_at FROM source_excerpts'):
                    value=json.loads(payload);stamp=value.get('fetched_at') or updated;ok=value.get('status')=='fetched'
                    db.execute('INSERT OR IGNORE INTO source_health VALUES (?,?,?,?,?,?,?,?,?,?,?)',(url,stamp,stamp if ok else '',value.get('status','failed'),value.get('error',''),error_kind(value),0 if ok else 1,'','',value.get('evidence_scope',''),value.get('reader') or ''))
            db.execute('CREATE TABLE IF NOT EXISTS reach_scope_repairs(url TEXT,hash TEXT,prior_run TEXT,reason TEXT,at TEXT,PRIMARY KEY(url,hash))')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_runs'").fetchone():
                rows=db.execute("SELECT s.url,s.hash,s.run_id,r.snapshot_json FROM source_reanalysis s JOIN strategic_workflow_runs r ON r.id=s.run_id WHERE s.status='failed' AND r.error='모든 원문을 위험 검토 또는 평가 불가로 구분해야 합니다.'").fetchall()
                for row in rows:
                    if not any(e.get('origin')=='external_source' for e in json.loads(row['snapshot_json'])):continue
                    saved=db.execute('INSERT OR IGNORE INTO reach_scope_repairs VALUES (?,?,?,?,?)',(row['url'],row['hash'],row['run_id'],'external_source_risk_coverage_v1',now()))
                    if saved.rowcount:db.execute("UPDATE source_reanalysis SET status='pending',run_id='',error='외부 원문 종류 검증 수정 후 1회 재처리',updated_at=? WHERE url=? AND hash=?",(now(),row['url'],row['hash']))
        self.thread=threading.Thread(target=self._loop,daemon=True,name='reach-scheduler')
        if autostart:self.thread.start()

    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db

    def submit(self,kind,payload,key=None):
        key=key or hashlib.sha256(json.dumps([kind,payload],sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id,status FROM reach_tasks WHERE task_key=?',(key,)).fetchone()
            if row:return dict(row)
            if db.execute("SELECT COUNT(*) FROM reach_tasks WHERE status IN ('queued','running','retry')").fetchone()[0]>=250:raise ValueError('외부 수집 대기열이 가득 찼습니다.')
            id=uuid.uuid4().hex;stamp=now()
            db.execute('INSERT INTO reach_tasks VALUES (?,?,?,?,?,0,0,?,?,NULL,?,?)',(id,key,kind,json.dumps(payload,ensure_ascii=False),'queued','{}','',stamp,stamp))
            db.execute("DELETE FROM reach_tasks WHERE status IN ('complete','failed') AND id NOT IN (SELECT id FROM reach_tasks ORDER BY created_at DESC LIMIT 1000)")
        return {'id':id,'status':'queued'}

    def repair(self,limit=50):
        limit=max(1,min(100,int(limit)));candidates=[]
        with self.db() as db:
            rows=db.execute('''SELECT s.canonical_url,s.result_json,h.next_retry,h.failures FROM source_excerpts s LEFT JOIN source_health h ON h.url=s.canonical_url
             ORDER BY CASE WHEN json_extract(s.result_json,'$.status')='fetched' THEN 1 ELSE 0 END,s.updated_at''').fetchall()
        for row in rows:
            r=json.loads(row['result_json']);kind=error_kind(r)
            if kind in ('blocked','unsupported','not_found') or (row['failures'] or 0)>=3:continue
            if row['next_retry'] and row['next_retry']>now():continue
            if r.get('status')=='fetched' and (r.get('full_content_hash') or not r.get('truncated')):continue
            candidates.append((row['canonical_url'],r))
            if len(candidates)>=limit:break
        jobs=[]
        for url,r in candidates:jobs.append(self.submit('repair',{'url':url,'mode':'reader' if error_kind(r) in ('empty','challenge','access_denied') else 'auto'},key='repair:'+url+':'+str(r.get('fetched_at',''))))
        return {'queued':len(jobs),'jobs':jobs}

    def add_subscription(self,payload):
        url=public_url(payload.get('url'));label=str(payload.get('label') or urlsplit(url).hostname)[:100];keywords=str(payload.get('keywords') or '')[:300]
        mode=payload.get('mode','rss')
        if mode not in ('rss','auto'):raise ValueError('RSS 또는 단일 자료 관측만 지원합니다.')
        interval=max(900,min(604800,int(payload.get('interval_seconds',21600))))
        id=hashlib.sha256(url.encode()).hexdigest()[:24]
        with self.db() as db:db.execute('''INSERT INTO reach_subscriptions VALUES (?,?,?,?,?,?,1,0,'','','') ON CONFLICT(url) DO UPDATE SET label=excluded.label,keywords=excluded.keywords,mode=excluded.mode,interval_seconds=excluded.interval_seconds,enabled=1''',(id,url,label,keywords,mode,interval))
        return {'id':id,'url':url}

    def toggle(self,id,enabled):
        if not isinstance(enabled,bool):raise ValueError('구독 활성 상태를 확인해 주세요.')
        with self.db() as db:
            if not db.execute('SELECT 1 FROM reach_subscriptions WHERE id=?',(id,)).fetchone():raise ValueError('구독을 찾을 수 없습니다.')
            db.execute('UPDATE reach_subscriptions SET enabled=? WHERE id=?',(int(enabled),id))
        return {'id':id,'enabled':enabled}

    def research(self,question,job_id):
        # One bounded public search per answer; its query is visible in the UI.
        return self.submit('research',{'question':question[:500],'answer_job_id':job_id},key='research:'+job_id)

    def _schedule(self):
        with self.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM reach_subscriptions WHERE enabled=1 AND next_at<=?',(time.time(),))]
        for r in rows:
            self.submit('subscription',r,key='subscription:'+r['id']+':'+str(int(r['next_at'])))
            with self.db() as db:db.execute('UPDATE reach_subscriptions SET next_at=? WHERE id=?',(time.time()+r['interval_seconds'],r['id']))

    def _loop(self):
        while not self.stop.is_set():
            try:
                self._schedule()
                with self.lock:
                    with self.db() as db:
                        db.execute('BEGIN IMMEDIATE')
                        rows=db.execute("SELECT * FROM reach_tasks WHERE status IN ('queued','retry') AND next_at<=? ORDER BY CASE kind WHEN 'research' THEN 0 WHEN 'subscription' THEN 1 ELSE 2 END,created_at LIMIT ?",(time.time(),max(0,2-len(self.active)))).fetchall()
                        for row in rows:db.execute("UPDATE reach_tasks SET status='running',attempts=attempts+1,owner=?,updated_at=? WHERE id=?",(os.getpid(),now(),row['id']))
                    for row in rows:self.active.add(row['id']);self.pool.submit(self._work,dict(row))
                if self.reviewer:self._review()
            except (sqlite3.Error,ValueError,RuntimeError):pass
            self.stop.wait(5)

    def _observe(self,url,result,label,published=''):
        if result.get('status')!='fetched' or result.get('last_attempt_status','fetched')!='fetched':return
        published=published or result.get('published_at','')
        try:published=datetime.fromisoformat(published.replace('Z','+00:00')).isoformat() if published else ''
        except ValueError:published=''
        with self.db() as db:
            db.execute('INSERT INTO reach_observations VALUES (?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title,text=excluded.text,published_at=excluded.published_at',
                (canonical_url(url),result.get('title',''),result.get('text',''),published,now(),label))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='projection_revisions'").fetchone():db.execute("UPDATE projection_revisions SET value=value+1 WHERE kind='source'")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='keyword_index_meta'").fetchone():
                from keyword_index import mark_keyword_source_changed
                mark_keyword_source_changed(db)

    def _work(self,row):
        payload=json.loads(row['payload']);source=None;result={};error='';status='complete';delay=900
        try:
            source=SourceService(self.path,fetcher=lambda url:self.invoke({'action':'read','url':url,'mode':payload.get('mode','auto')}))
            if row['kind']=='research':
                discovered=self.invoke({'action':'search','query':payload['question']+' primary source evidence limitations'})
                if discovered.get('status')!='fetched':raise RuntimeError(discovered.get('error') or '외부 검색을 완료하지 못했습니다.')
                saved=[]
                for e in discovered.get('entries',[])[:4]:
                    url=public_url(e.get('url'));r=source.fetch(url)
                    if r.get('status')=='fetched' and not r.get('full_content_hash'):r=source.fetch(url,refresh=True)
                    if r.get('status')=='fetched':saved.append({'url':url,'hash':r.get('full_content_hash',''),'scope':r.get('evidence_scope','')})
                result={'query':payload['question'],'sources':saved,'answer_job_id':payload['answer_job_id'],'verified':False}
                if not saved:raise RuntimeError('검색 결과에서 읽을 수 있는 원문을 확보하지 못했습니다.')
                from source_store import search
                from graph_rag import answer_question,GraphResult
                with self.db() as db:evidence=search(db,payload['question'],limit=8,urls=[e['url'] for e in saved])
                if evidence:
                    answer=answer_question(GraphResult({'nodes':[],'edges':[],'evidence':[]}),payload['question'],retrieval={'nodes':[],'edges':[],'evidence':evidence,'no_hits':False,'limitations':['추가로 확보한 공개 원문의 검토 결과입니다.']})
                    answer.update(phase='analysis',enrichment={'status':'complete','task_id':row['id'],'sources':saved})
                    result['answer']=answer;result['verified']=answer.get('verification',{}).get('method')=='independent_evidence_review'

            elif row['kind']=='subscription':
                fetched=self.invoke({'action':'read','url':payload['url'],'mode':payload['mode']})
                if fetched.get('status')!='fetched':raise RuntimeError(fetched.get('error') or '구독 확인 실패')
                if payload['mode']=='auto':
                    source.fetcher=lambda _:fetched
                    stored=source.fetch(payload['url'],refresh=True)
                    if stored.get('status')!='fetched':raise RuntimeError(stored.get('error','자료 본문 확인 실패'))
                    self._observe(payload['url'],stored,payload['label'],fetched.get('published_at',''))
                entries=fetched.get('entries',[])[:20] if payload['mode']=='rss' else []
                saved=[]
                # Articles are fetched individually: RSS summaries never masquerade as full text.
                for entry in entries:
                    try:url=public_url(entry.get('url'))
                    except ValueError:continue
                    self.submit('observation',{'url':url,'label':payload['label'],'published_at':entry.get('published_at','')},key='observation:'+url+':'+str(entry.get('published_at','')))
                    saved.append(url)
                result={'observations_queued':len(saved),'observations_updated':int(payload['mode']=='auto'),'urls':saved}
                with self.db() as db:db.execute("UPDATE reach_subscriptions SET last_checked=?,last_success=?,error='' WHERE id=?",(now(),now(),payload['id']))
            else:
                result=source.fetch(payload['url'],refresh=row['kind']=='repair')
                if result.get('status')=='fetched' and not result.get('full_content_hash'):result=source.fetch(payload['url'],refresh=True)
                if result.get('last_attempt_status',result.get('status'))!='fetched':
                    failure=dict(status=result.get('last_attempt_status',result.get('status')),error=result.get('last_attempt_error') or result.get('error',''))
                    kind=error_kind(failure);delay={'rate_limit':3600,'access_denied':21600,'challenge':21600}.get(kind,900)
                    if kind in ('not_found','blocked','unsupported'):row['attempts']=3
                    raise RuntimeError(failure['error'] or '원문 읽기 실패')
                if row['kind']=='observation':self._observe(payload['url'],result,payload['label'],payload.get('published_at',''))
                result={k:result.get(k) for k in ('url','full_content_hash','full_text_chars','evidence_scope','content_changed')}
        except Exception as exc:
            error=str(exc)[:500] if isinstance(exc,(ValueError,RuntimeError)) else '외부 자료 처리 오류: '+type(exc).__name__
            status='retry' if row['attempts']+1<3 else 'failed'
            if row['kind']=='subscription':
                with self.db() as db:db.execute('UPDATE reach_subscriptions SET last_checked=?,error=? WHERE id=?',(now(),error,payload['id']))
        finally:
            if source:source.close()
            with self.db() as db:db.execute('UPDATE reach_tasks SET status=?,result=?,error=?,next_at=?,updated_at=? WHERE id=?',
                (status,json.dumps(result,ensure_ascii=False),error,time.time()+delay*2**min(row['attempts'],3),now(),row['id']))
            with self.lock:self.active.discard(row['id'])

    def _review(self):
        from source_review_queue import classify, engine_blocked, archive, recovered_after
        from engine_errors import infrastructure_error
        # Do not create workflow rows, spend retry budgets or resume a checkpoint
        # while the shared engine gate is closed.
        if engine_blocked():
            with self.db() as db:
                db.execute("UPDATE source_reanalysis SET status='blocked_engine' WHERE status='pending'")
            return
        if self.workflow is None:
            from strategic_workflow import WorkflowService
            self.workflow=WorkflowService(self.path,recover_interrupted=False)
        if not self.workflow.enabled:return
        with self.db() as db:
            active=db.execute("SELECT * FROM source_reanalysis WHERE status IN ('running','blocked_engine') AND run_id!='' LIMIT 1").fetchone()
        if active:
            run=self.workflow.get_run(active['run_id'])
            engine_failure=run and run['status']=='failed' and infrastructure_error(run.get('error',''))
            recoverable=engine_failure and recovered_after(run.get('updated_at'))
            if engine_failure and not recoverable:
                with self.db() as db:db.execute("UPDATE source_reanalysis SET status='blocked_engine',error=? WHERE url=? AND hash=?",(run.get('error',''),active['url'],active['hash']))
                return
            if run and (run['status'] in ('queued','running','paused') or recoverable) and not self.workflow.active:
                from strategic_jobs import alive
                with self.db() as db:
                    request=json.loads(db.execute('SELECT request_json FROM strategic_workflow_runs WHERE id=?',(active['run_id'],)).fetchone()[0])
                    if not alive(request.get('owner_pid')) or recoverable:
                        archive(db,active['url'])
                        db.execute("UPDATE source_reanalysis SET status='running' WHERE url=? AND hash=?",(active['url'],active['hash']))
                        db.execute("UPDATE strategic_workflow_runs SET status='paused' WHERE id=? AND status IN ('queued','running')",(active['run_id'],))
                        db.commit();self.workflow.resume(active['run_id'])
                return
            if run and run['status'] in ('complete','needs_review','failed','interrupted'):
                status='blocked_engine' if infrastructure_error(run.get('error','')) else run['status']
                with self.db() as db:db.execute('UPDATE source_reanalysis SET status=?,error=?,updated_at=? WHERE url=? AND hash=? AND run_id=?',(status,run.get('error',''),now(),active['url'],active['hash'],active['run_id']))
            return
        if self.workflow.active:return
        with self.db() as db:
            row=db.execute("SELECT r.*,v.title,v.text FROM source_reanalysis r JOIN source_versions v ON v.url=r.url AND v.hash=r.hash WHERE r.status IN ('pending','blocked_engine') AND r.run_id='' ORDER BY r.updated_at LIMIT 1").fetchone()
            if not row:return
            disposition=classify(db,row)
            if disposition!='pending':
                archive(db,row['url'])
                db.execute('UPDATE source_reanalysis SET status=?,updated_at=? WHERE url=? AND hash=?',(disposition,now(),row['url'],row['hash']))
                return
        item={'source_url':row['url'],'title':row['title'],'text':row['text'][:2000],'origin':'external_source','date_basis':'unknown'}
        run=self.workflow.create_run([item],{'analysis_mode':'adaptive-v2','reason':'source_content_changed','source_hash':row['hash'],'source_url':row['url']})
        with self.db() as db:db.execute("UPDATE source_reanalysis SET status='running',run_id=?,updated_at=? WHERE url=? AND hash=? AND status IN ('pending','blocked_engine')",(run['id'],now(),row['url'],row['hash']))

    def get(self,id):
        with self.db() as db:r=db.execute('SELECT * FROM reach_tasks WHERE id=?',(id,)).fetchone()
        if not r:return None
        value=dict(r);value['result']=json.loads(value['result']);value['payload']=json.loads(value['payload']);return value

    def status(self):
        with self.db() as db:
            return dict(tasks=[dict(r) for r in db.execute('SELECT id,kind,status,attempts,error,created_at,updated_at FROM reach_tasks ORDER BY created_at DESC LIMIT 30')],
                counts=dict(db.execute('SELECT status,COUNT(*) FROM reach_tasks GROUP BY status')),
                subscriptions=[dict(r) for r in db.execute('SELECT * FROM reach_subscriptions ORDER BY label')],
                sources=[dict(r) for r in db.execute('SELECT * FROM source_health ORDER BY last_checked DESC LIMIT 30')],
                reanalysis=dict(db.execute('SELECT status,COUNT(*) FROM source_reanalysis GROUP BY status')),
                full_documents=db.execute('SELECT COUNT(DISTINCT url) FROM source_versions').fetchone()[0],
                observations=db.execute('SELECT COUNT(*) FROM reach_observations').fetchone()[0])

    def close(self):
        self.stop.set()
        if self.thread.is_alive():self.thread.join(timeout=2)
        self.pool.shutdown(wait=False,cancel_futures=True)
        if self.workflow:self.workflow.close()
