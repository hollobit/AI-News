"""Immediate cited excerpts followed by persisted, independently reviewed analysis."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import re
import sqlite3
import threading
import time
import uuid

from evidence_search import terms, query_anchors
from graph_snapshot import GraphSnapshots, filters
from graph_rag import answer_question, build_retrieval, GraphResult
from projection_cache import content_digest
from graph_quick_sources import cold_answer, setup_source_search

def passage(evidence,question,limit=500):
    text=evidence.get('text') or evidence.get('title') or ''
    # Keep contiguous source wording, including the context surrounding the hit.
    spans=list(re.finditer(r'[^\n.!?。]+[.!?。]?',text))
    query=set(terms(question))
    if not spans:return text[:limit]
    best=max(range(len(spans)),key=lambda i:len(set(terms(spans[i][0]))&query))
    start=spans[max(0,best-1)].start();end=spans[min(len(spans)-1,best+1)].end()
    if end-start>limit:start=spans[best].start();end=min(end,start+limit)
    return ('…' if start else '')+text[start:end].strip()+('…' if end<len(text) else '')

def quick_answer(graph,question,ids):
    retrieval=build_retrieval(graph,question,ids)
    evidence=retrieval['evidence'][:6];refs={e['id'] for e in evidence}
    claims=[{'text':passage(e,question),'evidence_ids':[e['id']],'kind':'source_excerpt'} for e in evidence[:4]]
    reviewed=[]
    index=graph.prepared_index
    for node in retrieval['nodes']:
        original=index.nodes[node['id']]
        if original.get('type')=='StrategicClaim' and set(original.get('evidence_ids',[]))<=refs:
            reviewed.append({'text':original.get('summary') or original['name'],
                             'evidence_ids':original['evidence_ids'],'kind':'previously_reviewed_analysis'})
    return {'phase':'evidence','answer':('관련 원문의 발췌와 기존 검토 내용을 먼저 제공합니다. 질문에 대한 추가 해석은 별도 검토 후 표시합니다.' if claims
        else '현재 저장 근거에서 질문에 관련된 자료를 찾지 못했습니다.'),
        'claims':claims,'reviewed_claims':reviewed[:3],'evidence':evidence,
        'selected_nodes':retrieval['nodes'],'selected_edges':retrieval['edges'],
        'limitations':['원문 발췌는 해당 출처의 주장입니다. 새 질문의 결론을 검증했다는 의미는 아닙니다.'],
        'verification':{'method':'source_excerpt_selection','question_answer_reviewed':False}}

def question_evidence(path,request,expected_ids=None):
    """Bounded unscoped fallback; never broaden a selected graph node or filter."""
    if request['node_ids'] or request['params']:return None
    from source_store import search
    from paper_context import retrieve
    quick=cold_answer(path,request['question'],[],{}) or {}
    with sqlite3.connect(path,timeout=10) as db:
        db.row_factory=sqlite3.Row
        paragraphs=search(db,request['question'],limit=6)
        papers,dependencies=retrieve(db,request['question'],limit=2)
        from knowledge_wiki import retrieve as wiki_sources
        wiki=wiki_sources(db,request['question'])
    evidence=list({e['id']:e for e in quick.get('evidence',[])+paragraphs+papers+wiki}.values())
    if expected_ids is not None:
        wanted=set(expected_ids)
        if not wanted<={e['id'] for e in evidence}:return None
        evidence=[e for e in evidence if e['id'] in wanted]
        paper_ids={e.get('paper_id') for e in evidence}
        dependencies=[p for p in dependencies if p['paper_id'] in paper_ids]
    if not evidence:return None
    evidence.sort(key=lambda e:e['id'])
    dependencies.sort(key=lambda p:p['paper_id'])
    for e in evidence:
        e.setdefault('source_kind','stored_news_excerpt')
        e.setdefault('evidence_origin',e.get('origin',e['source_kind']))
    # A newly edited wiki explanation is not a change to its original source.
    # Final claims are independently reviewed against these raw sources only.
    signature=content_digest({'evidence':[{k:v for k,v in e.items() if k not in ('wiki_context','wiki_page_id','wiki_revision')} for e in evidence],'papers':dependencies})
    graph=GraphResult(nodes=[],edges=[],evidence=evidence,coverage={'scope':'question_specific_evidence','full_graph_ready':False})
    graph.search_key=signature
    from graph_retrieval import RetrievalIndex
    graph.prepared_index=RetrievalIndex(graph)
    return graph,signature


def deepen(graph,question,ids,path=None,scoped=False):
    """Question planning adds no facts: retrieve each requested axis separately."""
    from semantic import run_structured
    from strategic_jobs import obj
    plan=run_structured('질문을 분석할 검색 계획만 작성한다. 사실에 답하거나 전제를 새로 만들지 않는다. '
        '질문에 명시된 대상과 비교 기준을 빠짐없이 나누어 최대 4개의 짧은 검색어를 queries에 적는다. '
        '등록 표현과 다른 한국어·영어 표현도 검색어로 사용할 수 있다. 필요한 반대 근거도 포함한다. '
        'axes에는 실제 질문의 비교 기준을 적는다.\n질문: '+question,
        obj({'queries':{'type':'array','minItems':1,'maxItems':4,'items':{'type':'string'}},
             'axes':{'type':'array','minItems':1,'maxItems':4,'items':{'type':'string'}}}),
        role='graph_answer',timeout=45,queue_timeout=15,reasoning_effort='low')
    if (not isinstance(plan,dict) or any(not isinstance(plan.get(k),list) or not 1<=len(plan[k])<=4
        or any(not isinstance(v,str) or not v.strip() or len(v)>300 for v in plan[k]) for k in ('queries','axes'))):
        raise RuntimeError('질문별 검색 계획 형식 오류')
    groups=[build_retrieval(graph,q,ids) for q in [question]+plan['queries']]
    selected={};nodes={};edges={};documents=set()
    # Round-robin prevents one comparison axis from consuming the whole context.
    for position in range(12):
        for group in groups:
            if position>=len(group['evidence']) or len(selected)>=16:continue
            e=group['evidence'][position];document=e.get('document_id') or e['id']
            if document in documents:continue
            selected[e['id']]=e;documents.add(document)
    for group in groups:
        nodes.update({n['id']:n for n in group['nodes']});edges.update({e['id']:e for e in group['edges']})
    if path:
        from source_store import search
        with sqlite3.connect(path,timeout=.5) as db:
            db.row_factory=sqlite3.Row
            allowed={e.get('source_url','') for e in selected.values()} if ids or scoped else None
            paragraphs=search(db,question,limit=8,urls=allowed)
            from knowledge_wiki import retrieve as wiki_sources
            wiki=[] if ids or scoped else wiki_sources(db,question,limit=1)
        # Keep multiple relevant passages of long documents, rather than only their first 3,500 characters.
        selected={**{e['id']:e for e in paragraphs+wiki},**selected}
    retrieval={'nodes':list(nodes.values())[:24],'edges':list(edges.values())[:40],
               'evidence':list(selected.values())[:20],'no_hits':not selected,'limitations':[]}
    for item in retrieval['nodes']+retrieval['edges']:
        item['evidence_ids']=[r for r in item.get('evidence_ids',[]) if r in {e['id'] for e in retrieval['evidence']}]
    # Pass the fixed retrieved bundle directly; do not rerank away a missing side.
    answer=answer_question(graph,question,ids,retrieval=retrieval,analysis_plan=plan)
    answer.update(phase='analysis',analysis_plan=plan)
    return answer

class GraphQuestions:
    def __init__(self,path,enabled=True,snapshots=None,analyzer=None,enricher=None):
        self.path=str(path);self.enabled=enabled
        setup_source_search(self.path)
        self.snapshots=snapshots or GraphSnapshots(path)
        self.analyzer=analyzer
        self.enricher=enricher
        self.lock=threading.RLock();self.active=set();self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='graph-answers')
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS graph_question_jobs (
                id TEXT PRIMARY KEY,key TEXT NOT NULL,revision TEXT NOT NULL,request_json TEXT NOT NULL,
                status TEXT NOT NULL,result_json TEXT NOT NULL,error TEXT NOT NULL,created_at REAL NOT NULL,owner_pid INTEGER)''')
            db.execute('CREATE INDEX IF NOT EXISTS graph_question_key ON graph_question_jobs(key,created_at)')
            # A process exit does not turn incomplete analysis into a verified answer.
            from strategic_jobs import alive
            for row in db.execute("SELECT id,owner_pid FROM graph_question_jobs WHERE status IN ('preparing','queued','running')"):
                if not alive(row['owner_pid']):
                    db.execute("UPDATE graph_question_jobs SET status='interrupted',error='서버 재시작으로 심층 분석이 중단되었습니다. 다시 질문하면 재개합니다.' WHERE id=?",(row['id'],))

    def db(self, timeout=.25):
        db=sqlite3.connect(self.path,timeout=timeout);db.row_factory=sqlite3.Row;return db

    def warm(self):
        try:self.snapshots.request({})
        except (RuntimeError,sqlite3.OperationalError):pass

    def ask(self,question,ids=None,params=None):
        started=time.monotonic();ids=ids or [];params=filters(params or {})
        if not isinstance(question,str) or not 3<=len(question.strip())<=2000:raise ValueError('질문을 확인해 주세요.')
        request={'question':question.strip(),'node_ids':sorted(set(ids)),'params':params}
        # Unrelated corpus work must not invalidate a reviewed, source-bounded
        # answer. Recheck its exact evidence before bypassing the global graph.
        with self.db() as db:
            candidates=db.execute("SELECT * FROM graph_question_jobs WHERE request_json=? AND status='complete' AND created_at>=? ORDER BY created_at DESC LIMIT 4",
                (json.dumps(request,ensure_ascii=False),time.time()-600)).fetchall()
        for candidate in candidates:
            saved=json.loads(candidate['result_json'])
            if saved.get('bounded_signature'):
                checked=self.get(candidate['id'])
                if checked['status']=='complete':return self._cached_answer(dict(candidate),started,checked)
        revision,graph,_=self.snapshots.request(params)
        key=content_digest(['two-phase-2-bounded',revision,request])
        job=None
        with self.lock:
            with self.db() as db:
                row=db.execute("SELECT * FROM graph_question_jobs WHERE key=? AND status IN ('preparing','queued','running','complete') ORDER BY created_at DESC LIMIT 1",(key,)).fetchone()
                if row:job=dict(row)
        if job and job['status']=='complete':
            return self._cached_answer(job,started)
        answer=quick_answer(graph,question,ids) if graph else cold_answer(self.path,question,ids,params)
        if answer is None:answer={
            'phase':'preparing','answer':'검색 근거를 준비하고 있습니다. 준비가 끝나면 이 화면에 표시됩니다.',
            'claims':[],'evidence':[],'limitations':[]}
        if not ids and not params:
            from source_store import search
            with self.db() as db:paragraphs=search(db,question,limit=4)
            if paragraphs:
                evidence=(paragraphs+answer.get('evidence',[]))[:8]
                answer.update(phase='evidence',answer='관련 원문의 문단을 먼저 제공합니다. 추가 해석은 독립 검토 후 표시합니다.',evidence=evidence,
                    claims=[{'text':passage(e,question),'evidence_ids':[e['id']],'kind':'source_excerpt'} for e in evidence[:4]],
                    verification={'method':'source_passage_selection','question_answer_reviewed':False})
        if not self.enabled and graph:answer['analysis_status']='disabled'
        if not graph or (self.enabled and (answer.get('evidence') or self.enricher)):
            with self.lock:
                with self.db() as db:
                    existing=db.execute("SELECT * FROM graph_question_jobs WHERE key=? AND status IN ('preparing','queued','running','complete') ORDER BY created_at DESC LIMIT 1",(key,)).fetchone()
                    if existing:
                        job=dict(existing)
                        if job['status']=='complete':
                            return self._cached_answer(job,started)
                    if not job:
                        if len(self.active)>=8:
                            answer['analysis_status']='busy'
                            answer['limitations'].append('심층 분석 대기열이 가득 찼습니다. 잠시 후 다시 질문해 주세요.')
                        else:
                            identity=uuid.uuid4().hex
                            job={'id':identity,'status':'queued' if graph else 'preparing'}
                            db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',
                                (identity,key,revision,json.dumps(request,ensure_ascii=False),job['status'],json.dumps(answer,ensure_ascii=False),'',time.time(),os.getpid()))
                    if job and job['id'] not in self.active:
                        self.active.add(job['id']);self.executor.submit(self._run,job['id'],revision,request)
            if job:answer['job']={'id':job['id'],'status':job['status'],'url':'/api/graph/answers/'+job['id']}
        answer['timing']={'total_ms':round((time.monotonic()-started)*1000,2),'answer_cache_hit':False}
        return answer

    def _cached_answer(self,job,started,current=None):
        current=current if current is not None else self.get(job['id'])
        answer=dict(current['result'])
        if current['status'] in ('queued','running','preparing'):
            answer['job']={'id':job['id'],'status':current['status'],'url':'/api/graph/answers/'+job['id']}
        answer['timing']={'total_ms':round((time.monotonic()-started)*1000,2),'answer_cache_hit':current['status']=='complete'}
        return answer

    def _save(self,identity,status,result,error=''):
        with self.db(timeout=10) as db:
            db.execute('UPDATE graph_question_jobs SET status=?,result_json=?,error=? WHERE id=?',
                       (status,json.dumps(result,ensure_ascii=False),error,identity))

    def _run(self,identity,revision,request,retries=1):
        with self.db(timeout=10) as db:
            stored=db.execute('SELECT result_json FROM graph_question_jobs WHERE id=?',(identity,)).fetchone()
        result=json.loads(stored[0]) if stored else {}
        try:
            begun=time.monotonic();deadline=begun+120;bounded_signature=None;fallback_checked=False
            # Unscoped questions can use a source-specific snapshot immediately;
            # unrelated ongoing analysis must not invalidate their citations.
            bounded = question_evidence(self.path, request) if not self.analyzer else None
            if bounded:
                graph, bounded_signature = bounded
            while not self.closed:
                if bounded_signature:break
                current,graph,future=self.snapshots.request(request['params'])
                if current!=revision:
                    # No analysis has begun: safely move the job to the current
                    # revision rather than fail merely because another job finished.
                    revision=current
                    with self.db(timeout=10) as db:
                        db.execute('UPDATE graph_question_jobs SET revision=?,key=? WHERE id=?',
                            (revision,content_digest(['two-phase-2-bounded',revision,request]),identity))
                if graph:break
                if not fallback_checked and time.monotonic()-begun>=5:
                    fallback_checked=True
                    bounded=question_evidence(self.path,request)
                    if bounded:
                        graph,bounded_signature=bounded
                        break
                if time.monotonic()>deadline:raise RuntimeError('검색 근거 준비 시간이 초과되었습니다.')
                time.sleep(.1)
            if self.closed:return
            result=quick_answer(graph,request['question'],request['node_ids'])
            self._save(identity,'running' if self.enabled else 'evidence_only',result)
            if not self.enabled or (not result.get('evidence') and not self.enricher):
                self._save(identity,'evidence_only',result);return
            if bounded_signature and not self.analyzer:
                retrieval={'nodes':[],'edges':[],'evidence':graph['evidence'],'no_hits':False,
                    'limitations':['질문에 직접 관련된 뉴스·원문·검토 논문으로 분석했습니다. 전체 관계 및 기간별 변화는 이 검색만으로 확정할 수 없습니다.']}
                answer=answer_question(graph,request['question'],retrieval=retrieval)
                answer.update(phase='analysis',evidence_scope='question_specific_evidence',bounded_signature=bounded_signature,bounded_evidence_ids=[e['id'] for e in graph['evidence']])
                answer.setdefault('limitations',[]).extend(retrieval['limitations'])
            else:
                answer=self.analyzer(graph,request['question'],request['node_ids']) if self.analyzer else deepen(graph,request['question'],request['node_ids'],path=self.path,scoped=bool(request['params']))
            latest=question_evidence(self.path,request,[e['id'] for e in graph['evidence']]) if bounded_signature else None
            changed=(not latest or latest[1]!=bounded_signature) if bounded_signature else self.snapshots.key(request['params'])!=revision
            if changed:
                if retries:
                    self._save(identity,'preparing',{},'근거 갱신으로 한 번 자동 재검토합니다.')
                    self._run(identity,revision,request,retries-1);return
                self._save(identity,'stale',{},'분석 중 근거가 갱신되어 새 해석을 표시하지 않습니다. 다시 질문해 주세요.');return
            gap=not answer.get('claims') or bool(re.search(r'미확보|자료.{0,20}부족|비교.{0,20}(불가|없)|판단.{0,20}없',str(answer.get('answer',''))+' '+str(answer.get('limitations',''))))
            if gap and self.enricher and not request['node_ids'] and not request['params']:
                task=self.enricher.research(request['question'],identity)
                answer['enrichment']={'status':task['status'],'task_id':task['id'],'note':'부족한 비교·반대 근거를 공개 검색하여 독립 검토합니다.'}

            self._save(identity,'complete',answer)
        except Exception as error:
            if isinstance(error,sqlite3.OperationalError):
                from engine_errors import EngineError
                message=str(EngineError('database_locked')) if 'locked' in str(error).lower() else '근거 저장소 조회 오류로 분석을 완료하지 못했습니다.'
            else:message=str(error) if isinstance(error,(RuntimeError,ValueError)) else '심층 분석을 완료하지 못했습니다 ('+type(error).__name__+').'
            self._save(identity,'failed',result,message)
        finally:
            with self.lock:self.active.discard(identity)

    def get(self,identity):
        with self.db() as db:row=db.execute('SELECT * FROM graph_question_jobs WHERE id=?',(identity,)).fetchone()
        if not row:return None
        result={'id':identity,'status':row['status'],'error':row['error'],'result':json.loads(row['result_json'])}
        if row['status'] in ('failed','interrupted','stale'):
            result['result']={}
            return result
        request=json.loads(row['request_json'])
        enrichment=result['result'].get('enrichment')
        if enrichment and self.enricher:
            task=self.enricher.get(enrichment['task_id'])
            if task and task['status'] in ('queued','running','retry'):
                result['status']='running';return result
            if task and task['status']=='complete' and task['result'].get('verified'):
                with self.db() as db:
                    current=all(db.execute("SELECT 1 FROM source_health WHERE url=? AND hash=? AND last_status='fetched'",(s['url'],s['hash'])).fetchone() for s in task['result']['sources'])
                if current:return {'id':identity,'status':'complete','error':'','result':task['result']['answer']}
            result['result']['enrichment']=dict(enrichment,status=task['status'] if task else 'failed',error=task.get('error','') if task else '작업 기록 없음')
        if result['result'].get('bounded_signature'):
            latest=question_evidence(self.path,request,result['result'].get('bounded_evidence_ids'))
            if not latest or latest[1]!=result['result']['bounded_signature']:
                return {'id':identity,'status':'stale','result':{},'error':'질문에 사용한 근거가 변경되어 재검토가 필요합니다.'}
            return result
        if self.snapshots.key(request['params'])!=row['revision']:
            if row['status'] in ('preparing','queued','running'):
                return {'id':identity,'status':'preparing','result':{},'error':'최신 근거를 준비하고 있습니다.'}
            return {'id':identity,'status':'stale','result':{},'error':'근거가 갱신되었습니다. 다시 질문해 주세요.'}
        return result

    def close(self):
        self.closed=True;self.executor.shutdown(wait=False,cancel_futures=True);self.snapshots.close()
