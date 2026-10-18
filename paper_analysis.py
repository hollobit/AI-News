"""Bounded parallel paper analyses with independent evidence review."""
import hashlib
import json
import os
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

CATEGORIES = ['problem', 'method', 'reported_result', 'comparison', 'limitation', 'reproducibility', 'keyword', 'strategic_implication', 'risk']
SOURCE_FIELDS = ('paper_id', 'title', 'abstract', 'authors', 'categories', 'primary_category', 'published', 'updated', 'doi', 'journal_ref', 'metadata_version', 'metadata_source', 'metadata_record_url', 'evidence_scope')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def analysis_input_hash(item):
    return digest({key: item.get(key) for key in SOURCE_FIELDS})


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


STR = {'type': 'string'}
STRS = {'type': 'array', 'items': STR}
CLAIM = obj({'category': {'type': 'string', 'enum': CATEGORIES}, 'title': STR, 'detail': STR, 'evidence_ids': STRS, 'uncertainty': STR})
REPORT_SCHEMA = obj({'summary': STR, 'claims': {'type': 'array', 'items': CLAIM, 'maxItems': 14}, 'limitations': STRS})
AUDIT_SCHEMA = obj({'accepted': {'type': 'boolean'}, 'issues': STRS, 'checked_evidence_ids': STRS, 'limitations': STRS})


def _owner_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def validate_report(report, evidence, keywords):
    ids = {e['id'] for e in evidence}
    if not isinstance(report, dict) or set(report) != set(REPORT_SCHEMA['required']) or not isinstance(report['summary'], str):
        raise ValueError('논문 분석 응답 형식 오류')
    if not isinstance(report['claims'], list) or not 1 <= len(report['claims']) <= 14:
        raise ValueError('논문 분석은 근거가 있는 항목 1~14개가 필요합니다.')
    if not isinstance(report['limitations'], list) or not report['limitations'] or any(not isinstance(v, str) for v in report['limitations']):
        raise ValueError('논문 분석의 자료 범위와 한계를 명시해야 합니다.')
    labels = {str(k.get('label', '')).strip().casefold() for k in keywords}
    for claim in report['claims']:
        if not isinstance(claim, dict) or set(claim) != set(CLAIM['required']) or claim['category'] not in CATEGORIES:
            raise ValueError('논문 분석 항목 형식 오류')
        if any(not isinstance(claim[k], str) or not claim[k].strip() for k in ('title', 'detail', 'uncertainty')):
            raise ValueError('논문 분석에 내용과 불확실성이 필요합니다.')
        refs = claim['evidence_ids']
        if not isinstance(refs, list) or not refs or any(not isinstance(ref,str) or ref not in ids for ref in refs):
            raise ValueError('논문 분석에 실제 제공되지 않은 근거가 인용되었습니다.')
        if claim['category'] == 'keyword' and claim['title'].strip().casefold() not in labels:
            raise ValueError('논문 키워드는 원문 형태소·기술 용어 후보를 사용해야 합니다.')
    return report


class PaperAnalysisService:
    def __init__(self, path, analyzer=None, enabled=None, fetcher=None, extractor=None, start_worker=True, workers=None):
        self.path = str(path)
        if analyzer is None:
            from semantic import run_structured
            analyzer = run_structured
        if fetcher is None:
            from source_content import fetch_source
            fetcher = fetch_source
        if extractor is None:
            from morphology import extract_keywords
            extractor = extract_keywords
        self.analyzer, self.fetcher, self.extractor = analyzer, fetcher, extractor
        self.enabled = os.environ.get('NEWS_EXTERNAL_ANALYSIS_ENABLED') == '1' if enabled is None else enabled
        self.closed = False
        self.workers=max(1,min(8,int(workers if workers is not None else os.environ.get('NEWS_PAPER_WORKERS','4'))))
        self.active = None
        self.lock = threading.RLock()
        self.wake = threading.Event()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS arxiv_paper_analyses (
                paper_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,snapshot_json TEXT NOT NULL,
                status TEXT NOT NULL,result_json TEXT,error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,owner_pid INTEGER,
                evidence_json TEXT,draft_json TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS arxiv_paper_analysis_history (
                paper_id TEXT NOT NULL,input_hash TEXT NOT NULL,result_json TEXT NOT NULL,updated_at TEXT NOT NULL,
                PRIMARY KEY(paper_id,input_hash))''')
            for row in db.execute("SELECT paper_id,owner_pid FROM arxiv_paper_analyses WHERE status='running'").fetchall():
                if not _owner_alive(row['owner_pid']):
                    db.execute("UPDATE arxiv_paper_analyses SET status='paused',owner_pid=NULL WHERE paper_id=?",(row['paper_id'],))
            from paper_recovery import setup
            setup(db)
            from paper_revision import setup as revision_setup
            revision_setup(db)
        self.thread = threading.Thread(target=self._worker, daemon=True, name='news-paper-analysis')
        if start_worker:
            self.thread.start()

    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _paper(self, identity):
        from arxiv_papers import paper, parse_arxiv_id
        parsed = parse_arxiv_id(identity)
        if not parsed:
            raise ValueError('유효한 arXiv 논문 ID가 필요합니다.')
        with self.db() as db:
            item = paper(db, parsed['paper_id'])
        if not item:
            raise ValueError('수집 기록에 있는 논문만 분석할 수 있습니다.')
        return item

    def submit(self, ids=None, limit=3):
        if not self.enabled or self.closed:
            raise RuntimeError('논문 분석 서비스가 종료되었거나 외부 분석이 비활성화되었습니다.')
        limit = max(1,min(int(limit),10))
        if ids is None:
            with self.db() as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='arxiv_papers'").fetchone()
                ids = [r[0] for r in db.execute('SELECT paper_id FROM arxiv_papers ORDER BY paper_id DESC')] if exists else []
        elif not isinstance(ids,list) or not all(isinstance(i,str) for i in ids):
            raise ValueError('논문 ID 목록이 필요합니다.')
        elif len(ids)>10:
            raise ValueError('한 번에 최대 10개 논문을 분석할 수 있습니다.')
        items=[]
        for identity in ids:
            item=self._paper(identity)
            if all(old['paper_id']!=item['paper_id'] for old in items):
                items.append(item)
            if len(items)>=limit:
                break
        queued,cached,skipped=[],[],[]
        with self.lock, self.db() as db:
            pending=db.execute("SELECT COUNT(*) FROM arxiv_paper_analyses WHERE status IN ('queued','running','paused')").fetchone()[0]
            if pending+len(items)>30:
                raise RuntimeError('논문 분석 대기열 상한은 30개입니다.')
            for item in items:
                identity=item['paper_id']
                if item.get('metadata_status')!='fetched' or not item.get('abstract') or not item.get('title'):
                    skipped.append({'paper_id':identity,'reason':'출처가 확인된 제목·초록 메타데이터 조회가 필요합니다.'})
                    continue
                current=db.execute('SELECT * FROM arxiv_paper_analyses WHERE paper_id=?',(identity,)).fetchone()
                signature=analysis_input_hash(item)
                if current and current['status']=='running' and _owner_alive(current['owner_pid']):
                    skipped.append({'paper_id':identity,'reason':'현재 버전 분석이 진행 중입니다.'})
                    continue
                if current and current['input_hash']==signature and current['status']=='complete':
                    cached.append(identity)
                    continue
                if current and current['input_hash']==signature and current['status'] in ('queued','paused'):
                    queued.append(identity)
                    continue
                if current and current['input_hash']==signature and current['status']=='failed':
                    db.execute("UPDATE arxiv_paper_analyses SET status='queued',error='',owner_pid=NULL,updated_at=? WHERE paper_id=?",(now(),identity))
                    queued.append(identity)
                    continue
                if current and current['result_json']:
                    db.execute('INSERT OR REPLACE INTO arxiv_paper_analysis_history VALUES (?,?,?,?)',
                               (identity,current['input_hash'],current['result_json'],current['updated_at']))
                if current and current['input_hash']==signature and current['status']=='needs_review':
                    from paper_revision import resume_state, save
                    state=resume_state(current)
                    if state is not None:
                        # Preserve the draft and rejection; only a current
                        # review can skip directly to correction. Automatic
                        # scheduling leaves semantic rejections on hold.
                        save(db,current,state)
                        db.execute("UPDATE arxiv_paper_analyses SET status='queued',error='',owner_pid=NULL,updated_at=? WHERE paper_id=?",
                                   (now(),identity))
                        import time
                        db.execute('INSERT INTO paper_analysis_events(paper_id,input_hash,stage,error_code,attempt,created_at) VALUES(?,?,?,?,?,?)',
                                   (identity,signature,'review_resume_requested','',len(state['history']),time.time()))
                        queued.append(identity)
                        continue
                db.execute('DELETE FROM paper_analysis_checkpoints WHERE paper_id=? AND input_hash=?',(identity,signature))
                stamp=now()
                db.execute('''INSERT OR REPLACE INTO arxiv_paper_analyses
                    (paper_id,input_hash,snapshot_json,status,result_json,error,created_at,updated_at,owner_pid,evidence_json,draft_json)
                    VALUES (?,?,?,'queued',NULL,'',?,?,NULL,NULL,NULL)''',
                    (identity,signature,json.dumps(item,ensure_ascii=False),stamp,stamp))
                queued.append(identity)
        self.wake.set()
        return {'queued':queued,'cached':cached,'skipped':skipped,'max_per_action':10,'scope':'known_arxiv_papers_only'}

    def _claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_pipeline_state'").fetchone():
                pipeline=db.execute('SELECT enabled FROM paper_pipeline_state WHERE id=1').fetchone()
                if pipeline and not pipeline[0]:return None
            rows=db.execute("SELECT * FROM arxiv_paper_analyses WHERE status IN ('queued','paused','running') ORDER BY draft_json IS NOT NULL DESC,created_at DESC").fetchall()
            row=next((r for r in rows if not _owner_alive(r['owner_pid'])),None)
            if row:
                db.execute("UPDATE arxiv_paper_analyses SET status='running',owner_pid=?,updated_at=? WHERE paper_id=?",(os.getpid(),now(),row['paper_id']))
                return dict(row)
        return None

    def _worker(self):
        pending={}
        with ThreadPoolExecutor(self.workers,thread_name_prefix='paper-analysis') as pool:
            while not self.closed:
                for future in list(pending):
                    if future.done():
                        future.result();pending.pop(future)
                try:
                    while self.enabled and not self.closed and len(pending)<self.workers:
                        row=self._claim()
                        if row is None:break
                        pending[pool.submit(self._execute,row)]=row['paper_id']
                except sqlite3.OperationalError:pass
                self.active=next(iter(pending.values()),None)
                self.wake.wait(.25 if pending else 2);self.wake.clear()
        self.active=None

    def _execute(self,row):
        try:
            self._analyze(row)
        except Exception as exc:
            self._save_failure(row,exc)

    def _save_failure(self,row,exc):
        # Checkpoint/error persistence may briefly wait for another writer, but
        # never take down the other paper workers or reset previous attempts.
        while True:
            try:
                with self.db() as db:
                    db.execute("UPDATE arxiv_paper_analyses SET status=?,error=?,owner_pid=NULL,updated_at=? WHERE paper_id=? AND input_hash=?",
                               ('paused' if self.closed else 'failed',str(exc)[:500],now(),row['paper_id'],row['input_hash']))
                    from paper_recovery import failed
                    failed(db,row,exc)
                return
            except sqlite3.OperationalError:
                if self.closed:return
                self.wake.wait(2);self.wake.clear()

    def _evidence(self,item):
        identity=item['paper_id']
        version=item.get('metadata_version')
        suffix=('v'+str(version).lstrip('v')) if version else ''
        url='https://arxiv.org/abs/'+identity+suffix
        provider=item.get('metadata_source') or 'arxiv_atom_api'
        secondary=provider in {'openalex','semantic_scholar'}
        if secondary:url=item['metadata_record_url']
        base={'paper_id':identity,'version':version,'title':item['title'],'source_url':url,'metadata_source':provider,'metadata_record_url':item.get('metadata_record_url',url),'published':item.get('published'),'updated':item.get('updated')}
        abstract=str(item['abstract'])[:6000]
        evidence=[dict(base,id='arxiv_abstract_'+digest([identity,version,abstract])[:24],text=abstract,origin='scholarly_index_abstract' if secondary else 'arxiv_abstract',truncated=len(item['abstract'])>6000)]
        metadata={k:item.get(k) for k in ('title','authors','categories','primary_category','published','updated','doi','journal_ref')}
        if isinstance(metadata.get('authors'),list):
            metadata['author_count']=len(metadata['authors']); metadata['authors']=metadata['authors'][:20]
        text=json.dumps(metadata,ensure_ascii=False)
        evidence.append(dict(base,id='arxiv_metadata_'+digest([identity,version,text])[:24],text=text,origin='scholarly_index_metadata' if secondary else 'arxiv_metadata'))
        if secondary:
            return {'evidence':evidence,'retrieval':{'url':url,'status':'fetched','limitation':'외부 학술 색인의 초록·메타데이터이며 arXiv 원문과 전문을 확인한 결과가 아닙니다.'},'scope':'secondary_index_metadata_and_abstract'}
        html_url='https://arxiv.org/html/'+identity+suffix
        try:
            with self.db() as db:
                cooldown=db.execute('SELECT retry_at FROM arxiv_api_cooldown WHERE id=1').fetchone() if db.execute("SELECT 1 FROM sqlite_master WHERE name='arxiv_api_cooldown'").fetchone() else None
            import time
            source={'status':'unavailable'} if cooldown and cooldown[0]>time.time() else self.fetcher(html_url)
        except Exception:
            source={'status':'failed'}
        body=str(source.get('text') or '')
        terms=set(re.findall(r'[a-z0-9]{3,}',item['title'].casefold()))
        found=sum(term in body[:6000].casefold() or term in str(source.get('title') or '').casefold() for term in terms)
        headings=sum(bool(re.search(r'\b'+term+r'\b',body,re.I)) for term in ('introduction','references','method','results','conclusion'))
        available=(source.get('status')=='fetched' and len(body)>=1500 and headings>=2 and bool(terms)
                   and found>=max(1,int(len(terms)*.6)) and urlparse(source.get('final_url') or html_url).hostname=='arxiv.org'
                   and not re.search(r'HTML (?:is |version is )?(?:not available|unavailable)|failed to convert',body[:1500],re.I))
        retrieval={'url':html_url,'status':'fetched' if available else 'unavailable','fetched_at':source.get('fetched_at') or now(),
                   'limitation':'HTML 일부 발췌만 사용하며 PDF 전체를 읽지 않았습니다.' if available else '연구 본문으로 확인 가능한 HTML을 확보하지 못해 공식 초록·메타데이터만 사용합니다.'}
        if available:
            chunks=[('start',body[:6000])]
            if len(body)>6000:
                chunks.append(('end',body[-6000:] if len(body)>12000 else body[6000:]))
            for position,chunk in chunks:
                evidence.append(dict(base,id='arxiv_html_'+digest([identity,version,position,chunk])[:24],text=chunk,
                                     origin='arxiv_html_excerpt',source_url=html_url,position=position,truncated=True))
        return {'evidence':evidence,'retrieval':retrieval,'scope':'abstract_and_html_excerpts' if available else 'abstract_and_official_metadata_only'}

    def _prompt(self,role,item,bundle,keywords,report=None):
        from paper_prompts import render
        return render(role,item,bundle,keywords,report)

    def _analyze(self,row):
        from paper_revision import load, save, checkpoint, prompt as repair_prompt, schema as repair_schema, apply as apply_repair
        item=json.loads(row['snapshot_json'])
        bundle=json.loads(row['evidence_json']) if row.get('evidence_json') else self._evidence(item)
        with self.db() as db:
            db.execute('UPDATE arxiv_paper_analyses SET evidence_json=? WHERE paper_id=? AND input_hash=?',
                       (json.dumps(bundle,ensure_ascii=False),row['paper_id'],row['input_hash']))
        keywords=self.extractor(item['title']+'\n'+item['abstract'][:6000])[:40]
        draft=json.loads(row['draft_json']) if row.get('draft_json') else None
        report=validate_report(draft or self.analyzer(self._prompt('analysis',item,bundle,keywords),REPORT_SCHEMA),bundle['evidence'],keywords)
        with self.db() as db:
            db.execute('UPDATE arxiv_paper_analyses SET draft_json=? WHERE paper_id=? AND input_hash=?',
                       (json.dumps(report,ensure_ascii=False),row['paper_id'],row['input_hash']))
            state=load(db,row,report,bundle['evidence'])
            save(db,row,state)
        while True:
            if self.closed:
                with self.db() as db:
                    db.execute("UPDATE arxiv_paper_analyses SET status='paused',owner_pid=NULL WHERE paper_id=?",(row['paper_id'],))
                return
            if state['stage']=='repair':
                issues=state['history'][-1]['verification']['issues']
                patch=self.analyzer(repair_prompt(item,bundle,keywords,report,issues),repair_schema(report))
                report=apply_repair(report,patch,bundle['evidence'],keywords)
                state=checkpoint(report,bundle['evidence'],history=state['history'],repairs=1)
                with self.db() as db:
                    db.execute('UPDATE arxiv_paper_analyses SET draft_json=? WHERE paper_id=? AND input_hash=?',
                               (json.dumps(report,ensure_ascii=False),row['paper_id'],row['input_hash']))
                    save(db,row,state)
                continue
            audit=self.analyzer(self._prompt('verification',item,bundle,keywords,report),AUDIT_SCHEMA)
            ids={e['id'] for e in bundle['evidence']}
            refs={ref for c in report['claims'] for ref in c['evidence_ids']}
            if (not isinstance(audit,dict) or not isinstance(audit.get('accepted'),bool) or not isinstance(audit.get('issues'),list)
                    or not all(isinstance(v,str) for v in audit['issues']) or not isinstance(audit.get('checked_evidence_ids'),list)
                    or any(not isinstance(ref,str) or ref not in ids for ref in audit['checked_evidence_ids'])
                    or not isinstance(audit.get('limitations'),list) or any(not isinstance(v,str) for v in audit['limitations'])):
                raise ValueError('논문 독립 검증 형식 또는 인용 오류')
            if not refs<=set(audit['checked_evidence_ids']):
                audit['issues'].append('모든 주장 인용 근거를 대조하지 않았습니다.')
            audit['accepted']=audit['accepted'] and not audit['issues']
            from paper_prompts import REVIEW_VERSION
            audit.update(report_hash=digest(report),evidence_hash=digest(bundle['evidence']),review_policy=REVIEW_VERSION)
            if audit['accepted'] or state['repairs']==1:break
            state['history'].append({'report':report,'verification':dict(audit)})
            state['stage']='repair'
            with self.db() as db:
                save(db,row,state)
        result=dict(bundle,report=report,revision_history=state['history'],verification=audit,verified=audit['accepted'],paper_id=item['paper_id'],version=item.get('metadata_version'),
                    input_hash=row['input_hash'],keywords=keywords,analyzed_at=now())
        with self.db() as db:
            db.execute('UPDATE arxiv_paper_analyses SET status=?,result_json=?,error=?,owner_pid=NULL,updated_at=? WHERE paper_id=? AND input_hash=?',
                       ('complete' if audit['accepted'] else 'needs_review',json.dumps(result,ensure_ascii=False),'',now(),row['paper_id'],row['input_hash']))

    def get(self,identity, *, item=None):
        item=item if item is not None else self._paper(identity)
        with self.db() as db:
            row=db.execute('SELECT * FROM arxiv_paper_analyses WHERE paper_id=?',(item['paper_id'],)).fetchone()
        if not row:
            return {'paper_id':item['paper_id'],'status':'not_analyzed','stale':False,'result':None,'error':''}
        result=json.loads(row['result_json']) if row['result_json'] else None
        stale=analysis_input_hash(item)!=row['input_hash']
        status='stale' if stale and row['status'] in ('complete','needs_review') else row['status']
        return {'paper_id':item['paper_id'],'status':status,'stale':stale,'input_hash':row['input_hash'],
                'snapshot':json.loads(row['snapshot_json']),'result':result,'error':row['error'],'updated_at':row['updated_at']}

    def status(self):
        with self.db() as db:
            counts={r[0]:r[1] for r in db.execute('SELECT status,COUNT(*) FROM arxiv_paper_analyses GROUP BY status')}
            active = [r[0] for r in db.execute("SELECT paper_id FROM arxiv_paper_analyses WHERE status='running' ORDER BY updated_at")]
            from paper_recovery import summary
            recovery=summary(db)
        return {'enabled':self.enabled,'pending':sum(counts.get(s,0) for s in ('queued','paused')),'running': active[0] if active else None,
                'running_ids':active,'counts':counts,'workers':self.workers,'recovery':recovery,'max_per_action':10}

    def close(self):
        self.closed=True
        self.wake.set()
