import json
import sqlite3
import threading
import time
from concurrent.futures import Future,ThreadPoolExecutor
from unittest.mock import patch
from graph_quick_sources import cold_answer
from graph_questions import GraphQuestions


def source_db(path):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE news(chat_id,message_id,channel)')
        db.execute('CREATE TABLE articles(chat_id,message_id,item_index,title,excerpt,text,day,topic,source_url,kind)')
        db.execute("INSERT INTO news VALUES (1,1,'test')")
        db.execute("INSERT INTO articles VALUES (1,1,0,'Semiconductor supply','Observed supply constraint','Semiconductor supply constraints remain. This source does not predict a crisis.','2026-09-16','tech','https://example.org/a','news')")


def test_cold_lookup_alias_citations_and_scope(tmp_path):
    path=tmp_path/'news.db';source_db(path)
    result=cold_answer(path,'반도체 공급 상황',[],{})
    assert result['evidence'][0]['source_url']=='https://example.org/a'
    assert not result['verification']['question_answer_reviewed']
    assert result['claims'][0]['evidence_ids']==[result['evidence'][0]['id']]
    assert cold_answer(path,'반도체 공급 상황',['node:1'],{}) is None
    assert cold_answer(path,'반도체 공급 상황',[],{'lens':['x']}) is None
    assert cold_answer(path,'반도체 공급 상황',[],{'date':['2026-09-15']}) is None
    assert cold_answer(path,'반도체 공급 상황',[],{'channel':['wrong']}) is None


class Snapshots:
    def request(self,params):return 'rev',object(),None
    def key(self,params):return 'rev'
    def close(self):pass


def test_workspace_preview_and_synthesis_share_full_question_graph(tmp_path,monkeypatch):
    from context_index import write,read,materialize_many
    from graph_question_context import compose,searches_for
    from tests.test_context_index import source
    sources=[source('baseline','a','반도체 평가','반도체 실험 성공률은 90%다.'),
             source('paper','b','로봇 평가','로봇 현장 평가 성공률은 40%다.')]
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:(sources,{}))
    index=tmp_path/'index.db'
    with sqlite3.connect(':memory:') as db:write(index,db,'rev')
    graph=read(index,'rev')
    question='반도체와 로봇 평가 비교'
    plan={'queries':['반도체 평가','로봇 현장 평가'],'axes':['반도체','로봇']}
    expected=compose(read(index,'rev'),question,[],plan)
    class Workspace(Snapshots):
        def request(self,params):return 'rev',graph,None
    q=GraphQuestions(tmp_path/'jobs.db',snapshots=Workspace())
    seen=[]
    def answer(graph,question,ids,**kwargs):
        seen.append(kwargs['retrieval'])
        assert hasattr(graph,'prepared_index') and not hasattr(graph,'workspace_store')
        return {'answer':'검토된 비교','claims':[{'text':'비교'}],'evidence':kwargs['retrieval']['evidence'],
                'verification':{'method':'independent_evidence_review','quality':{'policy':'question-explanation-v1','status':'passed'}}}
    try:
        with patch('graph_plans.prepare',return_value=(plan,True)),patch('graph_questions.answer_question',side_effect=answer),\
                patch('decision_tasks.retrieval'),patch('context_current.current',return_value=True),\
                patch('context_index.materialize_many',wraps=materialize_many) as materialize:
            first=q.ask(question,params={'workspace':['1']})
            deadline=time.monotonic()+3
            while q.active and time.monotonic()<deadline:time.sleep(.01)
            done=q.get(first['job']['id'])
            assert done['status']=='complete',done
            assert materialize.call_count==1
            assert materialize.call_args.args[1]==searches_for(question,plan)
            assert len(seen)==1 and seen[0]['evidence']==expected['evidence']
            assert seen[0]['nodes']==expected['nodes'] and seen[0]['edges']==expected['edges']
    finally:q.close()


def test_bounded_cache_survives_unrelated_revision_but_checks_sources(tmp_path):
    q=GraphQuestions(tmp_path/'bounded.db',snapshots=Snapshots())
    request={'question':'같은 질문 테스트','node_ids':[],'params':{}}
    result={'phase':'analysis','answer':'reviewed','bounded_signature':'same','bounded_evidence_ids':['e']}
    try:
        with q.db() as db:
            db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',('id','old-key','old',json.dumps(request,ensure_ascii=False),'complete',json.dumps(result),'',time.time(),0))
        with patch('graph_questions.question_evidence',return_value=(None,'same')):
            answer=q.ask(request['question'])
            assert answer['timing']['answer_cache_hit'] and answer['answer']=='reviewed'
        with patch('graph_questions.question_evidence',return_value=(None,'changed')):
            assert q.get('id')['status']=='stale'
            assert not q._cached_answer({'id':'id'},time.monotonic())['timing']['answer_cache_hit']
    finally:q.close()


def test_concurrent_questions_share_one_analysis_and_cache(tmp_path):
    gate=threading.Event();calls=[]
    def analyze(*args):
        calls.append(1);gate.wait(2)
        return {'phase':'analysis','answer':'reviewed','evidence':[{'id':'e'}]}
    path=tmp_path/'jobs.db'
    # Production prepare_database uses WAL. The rollback-journal default can
    # block concurrent readers behind a commit on a loaded CI filesystem.
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    q=GraphQuestions(path,snapshots=Snapshots(),analyzer=analyze)
    evidence={'phase':'evidence','evidence':[{'id':'e'}],'limitations':[]}
    try:
        with patch('graph_questions.quick_answer',side_effect=lambda *args:dict(evidence)):
            with ThreadPoolExecutor(max_workers=8) as pool:
                answers=list(pool.map(lambda _:q.ask('같은 질문 테스트'),range(8)))
            assert len({a['job']['id'] for a in answers})==1
            gate.set()
            deadline=time.monotonic()+3
            while q.active and time.monotonic()<deadline:time.sleep(.01)
            assert len(calls)==1
            answer=q.ask('같은 질문 테스트')
            assert answer['timing']['answer_cache_hit'] and answer['phase']=='analysis'
            assert len(calls)==1
    finally:gate.set();q.close()


def test_worker_can_observe_job_on_its_first_instruction(tmp_path):
    path=tmp_path/'committed.db'
    with sqlite3.connect(path) as db:db.execute('PRAGMA journal_mode=WAL')
    q=GraphQuestions(path,snapshots=Snapshots(),analyzer=lambda *args:{'phase':'analysis','answer':'reviewed','evidence':[{'id':'e'}]})
    q.executor.shutdown(wait=True)
    class ImmediateWorker:
        def submit(self,fn,identity,*args):
            # A separate worker connection must see the job before any work.
            with sqlite3.connect(path) as db:
                row=db.execute('SELECT status FROM graph_question_jobs WHERE id=?',(identity,)).fetchone()
            assert row is not None, 'Worker started before the job was committed'
            assert row[0]=='queued'
            fn(identity,*args)
        def shutdown(self,**kwargs):pass
    q.executor=ImmediateWorker()
    try:
        with patch('graph_questions.quick_answer',return_value={'phase':'evidence','evidence':[{'id':'e'}],'limitations':[]}):
            answer=q.ask('저장 후 실행하는 질문')
            assert q.get(answer['job']['id'])['status']=='complete'
    finally:q.close()


def test_revision_invalidates_job(tmp_path):
    snapshots=Snapshots();q=GraphQuestions(tmp_path/'jobs.db',enabled=False,snapshots=snapshots)
    try:
        with q.db() as db:
            db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',('id','key','old',json.dumps({'params':{}}),'complete','{}','',time.time(),0))
        assert q.get('id')['status']=='stale'
        with q.db() as db:db.execute("UPDATE graph_question_jobs SET status='failed',error='original failure' WHERE id='id'")
        failed=q.get('id')
        assert failed['status']=='failed' and failed['error']=='original failure' and failed['result']=={}
    finally:q.close()


def test_analysis_retries_once_after_revision_change(tmp_path):
    class ChangingSnapshots(Snapshots):
        revision='r1'
        def request(self,params):return self.revision,object(),None
        def key(self,params):return self.revision
    snapshots=ChangingSnapshots();calls=[]
    def analyze(*args):
        calls.append(1)
        if len(calls)==1:snapshots.revision='r2'
        return {'phase':'analysis','answer':'reviewed','evidence':[{'id':'e'}]}
    q=GraphQuestions(tmp_path/'retry.db',snapshots=snapshots,analyzer=analyze)
    try:
        with patch('graph_questions.quick_answer',return_value={'phase':'evidence','evidence':[{'id':'e'}],'limitations':[]}):
            a=q.ask('갱신되는 질문')
            deadline=time.monotonic()+3
            while q.active and time.monotonic()<deadline:time.sleep(.01)
            assert len(calls)==2
            assert q.get(a['job']['id'])['status']=='complete'
            assert q.ask('갱신되는 질문')['timing']['answer_cache_hit']
    finally:q.close()


def test_inflight_same_question_is_shared_across_corpus_revisions(tmp_path):
    gate=threading.Event();entered=threading.Event();calls=[]
    class ChangingSnapshots(Snapshots):
        revision='r1'
        def request(self,params):return self.revision,object(),None
        def key(self,params):return self.revision
    snapshots=ChangingSnapshots()
    def analyze(*args):
        calls.append(1);entered.set();gate.wait(3)
        return {'answer':'reviewed','claims':[{'text':'fact'}],'evidence':[{'id':'e'}]}
    q=GraphQuestions(tmp_path/'share.db',snapshots=snapshots,analyzer=analyze)
    try:
        with patch('graph_questions.quick_answer',return_value={'evidence':[{'id':'e'}],'limitations':[]}):
            first=q.ask('같은 질문 재사용')
            assert entered.wait(2)
            snapshots.revision='r2'
            second=q.ask('같은 질문 재사용')
            assert first['job']['id']==second['job']['id'] and len(calls)==1
            gate.set()
            deadline=time.monotonic()+3
            while q.active and time.monotonic()<deadline:time.sleep(.01)
            done=q.get(first['job']['id'])
            assert done['status']=='complete' and len(calls)==2
            assert done['result']['timing']['completion_ms'] >= done['result']['timing']['attempt_ms']
    finally:gate.set();q.close()


def test_final_revision_check_can_reuse_review_without_third_generation(tmp_path):
    class ChangingSnapshots(Snapshots):
        revision='r1'
        def request(self,params):return self.revision,object(),None
        def key(self,params):return self.revision
    snapshots=ChangingSnapshots();calls=[]
    reviewed={'answer':'검토된 답변','claims':[{'text':'근거'}], 'input_signature':'same',
              'verification':{'method':'independent_evidence_review','quality':{'policy':'question-explanation-v1','status':'passed'}}}
    def analyze(*args,reuse_only=None,**kwargs):
        calls.append(reuse_only)
        if len(calls)<3:snapshots.revision='r'+str(len(calls)+1)
        else:assert reuse_only['input_signature']=='same'
        return dict(reviewed,timing={'answer_cache_hit':reuse_only is not None,'answer_ms':1})
    q=GraphQuestions(tmp_path/'confirm.db',snapshots=snapshots)
    try:
        with patch('graph_questions.deepen',side_effect=analyze),patch('graph_plans.prepare',return_value=({'queries':['근거'],'axes':['조건']},True)),patch('graph_questions.quick_answer',return_value={'evidence':[{'id':'e'}],'limitations':[]}):
            first=q.ask('최종 근거 확인',params={'workspace':['1']})
            deadline=time.monotonic()+3
            while q.active and time.monotonic()<deadline:time.sleep(.01)
            done=q.get(first['job']['id'])
            assert done['status']=='complete' and len(calls)==3
            assert calls[:2]==[None,None]
            assert len(done['result']['timing']['attempts'])==3
            assert done['result']['verification']==reviewed['verification']
    finally:q.close()


def test_incremental_cold_index_tracks_edits_and_deletions(tmp_path):
    from graph_quick_sources import setup_source_search
    path=tmp_path/'sources.db';source_db(path)
    assert setup_source_search(path)
    assert cold_answer(path,'반도체 공급 상황',[],{})
    with sqlite3.connect(path) as db:
        db.execute("UPDATE articles SET title='New robotics',excerpt='',text='Robotics field observation'")
    assert cold_answer(path,'반도체 공급 상황',[],{}) is None
    assert cold_answer(path,'Robotics field observation',[],{})
    with sqlite3.connect(path) as db:db.execute('DELETE FROM articles')
    assert cold_answer(path,'Robotics field observation',[],{}) is None
    assert setup_source_search(path)


def test_cached_answer_keeps_polling_background_enrichment(tmp_path):
    class Enricher:
        def research(self,*args):return {'id':'external','status':'queued'}
        def get(self,id):return {'status':'queued','result':{},'error':''}
    q=GraphQuestions(tmp_path/'jobs.db',snapshots=Snapshots(),enricher=Enricher(),analyzer=lambda *args:{'answer':'비교 자료가 부족합니다.','claims':[],'limitations':['자료 부족']})
    try:
        with patch('graph_questions.quick_answer',return_value={'phase':'evidence','evidence':[{'id':'e'}],'limitations':[]}):
            a=q.ask('비교 자료 질문')
            deadline=time.monotonic()+3
            while q.active and time.monotonic()<deadline:time.sleep(.01)
            cached=q.ask('비교 자료 질문')
            assert cached['job']['id']==a['job']['id']
            assert cached['job']['status']=='running' and not cached['timing']['answer_cache_hit']
    finally:q.close()


def test_question_fallback_uses_real_evidence_and_never_broadens_scope(tmp_path):
    from graph_questions import question_evidence
    path=tmp_path/'scope.db';source_db(path)
    request={'question':'반도체 공급 상황','node_ids':[],'params':{}}
    with patch('source_store.search',return_value=[]),patch('paper_context.retrieve',return_value=([],[])):
        graph,signature=question_evidence(path,request)
        assert graph['evidence'][0]['source_url']=='https://example.org/a'
        from graph_questions import quick_answer
        assert quick_answer(graph,request['question'],[])['evidence']
        assert question_evidence(path,dict(request,node_ids=['selected'])) is None
        assert question_evidence(path,dict(request,params={'lens':['medical']})) is None
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO news VALUES (1,2,'test')")
            db.execute("INSERT INTO articles SELECT chat_id,2,item_index,title,excerpt,text,day,topic,'https://example.org/b',kind FROM articles LIMIT 1")
        assert question_evidence(path,request,[e['id'] for e in graph['evidence']])[1]==signature
        with sqlite3.connect(path) as db:db.execute("UPDATE articles SET text='Semiconductor supply improved.'")
        assert question_evidence(path,request)[1]!=signature


def test_completed_bounded_answer_checks_its_sources_not_unrelated_graph_revision(tmp_path):
    q=GraphQuestions(tmp_path/'bounded.db',enabled=False,snapshots=Snapshots())
    request={'question':'근거 확인 질문','node_ids':[],'params':{}}
    try:
        with q.db() as db:db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',
            ('id','key','old',json.dumps(request),'complete',json.dumps({'bounded_signature':'same','answer':'검토된 답변'}),'',time.time(),0))
        with patch('graph_questions.question_evidence',return_value=({},'same')):
            assert q.get('id')['status']=='complete'
        with patch('graph_questions.question_evidence',return_value=({},'changed')):
            assert q.get('id')['status']=='stale'
    finally:q.close()


def test_background_warmup_is_idempotent_and_stops_without_analysis(tmp_path):
    from graph_questions import GraphQuestions
    class Snapshots:
        def __init__(self):self.calls=0;self.closed=False
        def request(self,params):
            assert params=={'workspace':['1']}
            self.calls+=1
        def close(self):self.closed=True
    snapshots=Snapshots()
    service=GraphQuestions(tmp_path/'warm.sqlite3',snapshots=snapshots)
    service.warm();worker=service.warm_thread;service.warm()
    assert service.warm_thread is worker and worker.is_alive()
    assert snapshots.calls==2 and not service.active
    service.close()
    assert not worker.is_alive() and snapshots.closed
    service.warm()
    assert snapshots.calls==2


def test_workspace_answers_validate_selected_sources_not_unrelated_revision(tmp_path):
    from verified_cache import policy
    q=GraphQuestions(tmp_path/'selected.db',snapshots=Snapshots())
    request={'question':'관계 종합 분석 질문','node_ids':[],'params':{'workspace':['1']}}
    from graph_questions import answer_day
    result={'phase':'analysis','answer':'검토된 종합 설명','claims':[{'text':'근거'}],'answer_as_of':answer_day(),
            'workspace_dependencies':{'source':'hash'},'workspace_as_of':time.time(),'workspace_policy':policy(),
            'verification':{'method':'independent_evidence_review','quality':{'policy':'question-explanation-v1','status':'passed'}}}
    try:
        with q.db() as db:
            db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',('selected','old-key','old',json.dumps(request,ensure_ascii=False),'complete',json.dumps(result),'',time.time(),0))
        with patch('context_current.current',return_value=True) as validate:
            answer=q.ask(request['question'],params=request['params'])
            assert answer['timing']['answer_cache_hit'] and answer['answer']==result['answer']
            validate.assert_called()
        with patch('context_current.current',return_value=False):
            assert q.get('selected')['status']=='stale'
        with patch('context_current.current',return_value=True),patch('verified_cache.policy',return_value='new-review-policy'):
            assert q.get('selected')['status']=='stale'
    finally:q.close()


def test_workspace_progress_does_not_go_back_to_preparation_on_unrelated_writes(tmp_path):
    q=GraphQuestions(tmp_path/'progress.db',snapshots=Snapshots())
    try:
        with q.db() as db:
            db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',('id','key','old',json.dumps({'params':{'workspace':['1']}}),'running','{}','',time.time(),0))
        assert q.get('id')['status']=='running'
    finally:q.close()


def test_consistent_workspace_fallback_preserves_selected_nodes_and_filters(tmp_path):
    for index,(params,ids,expected) in enumerate([
            ({'workspace':['1']},[],True),
            ({'workspace':['1'],'topic':['robotics']},[],False),
            ({'workspace':['1']},['node:1'],False)]):
        class Preparing(Snapshots):
            def request(self,params):return 'rev',None,None
            def available_workspace(self):return object()
        snapshots=Preparing()
        q=GraphQuestions(tmp_path/(str(index)+'.db'),enabled=False,snapshots=snapshots)
        request={'question':'범위 지정 질문','node_ids':ids,'params':params}
        try:
            with q.db() as db:
                db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',('id','key','rev',json.dumps(request),'preparing','{}','',time.time(),0))
            with patch.object(snapshots,'available_workspace',wraps=snapshots.available_workspace) as available,patch('graph_questions.quick_answer',return_value={'evidence':[{'id':'e'}]}),patch('graph_questions.time.sleep',side_effect=lambda _:setattr(q,'closed',True)):
                q._run('id','rev',request)
                assert bool(available.call_count)==expected
        finally:q.close()
