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
    q=GraphQuestions(tmp_path/'jobs.db',snapshots=Snapshots(),analyzer=analyze)
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
