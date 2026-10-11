import sqlite3
import json
import pytest
from unittest.mock import patch
from question_progress import setup, update, view, STAGES
from graph_plans import prepare


def test_search_plan_reused_without_evidence_or_extra_model_calls(tmp_path):
    path=tmp_path/'plans.db';plan={'queries':['벤치마크 방법','데이터 종류'],'axes':['방법','데이터']}
    with patch('semantic.run_structured',return_value=plan) as runner:
        assert prepare('평가 방법과 데이터는?',path)==(plan,False)
        assert prepare('평가 방법과 데이터는?',path)==(plan,True)
        assert runner.call_count==1


def test_progress_counts_completed_stages_not_elapsed_time():
    db=sqlite3.connect(':memory:');setup(db)
    db.execute('CREATE TABLE graph_question_jobs(id,status)')
    db.execute("INSERT INTO graph_question_jobs VALUES('j','running')")
    with patch('question_progress.time.time',return_value=100):
        update(db,'j','evidence');update(db,'j','plan')
    with patch('question_progress.time.time',return_value=115):
        update(db,'j','plan',done=True)
    with patch('question_progress.time.time',return_value=160):
        p=view(db,'j','preparing',95)
        assert p['percent']==20 and p['completed']==1 and p['active']==['evidence']
        assert p['elapsed_seconds']==65 and p['remaining_seconds'] is None
        update(db,'j','plan',done=True)
        assert view(db,'j','preparing',95)['stages'][1]['seconds']==15


def test_progress_eta_uses_recorded_stages_and_parallel_preparation():
    db=sqlite3.connect(':memory:');setup(db)
    db.execute('CREATE TABLE graph_question_jobs(id,status)')
    db.executemany('INSERT INTO graph_question_jobs VALUES(?,?)',[('previous','complete'),('new','running')])
    for stage,begin,end in zip(STAGES,[100,100,140,200,230],[140,120,200,230,231]):
        with patch('question_progress.time.time',return_value=begin):update(db,'previous',stage)
        with patch('question_progress.time.time',return_value=end):update(db,'previous',stage,done=True)
    with patch('question_progress.time.time',return_value=300):
        update(db,'new','evidence');update(db,'new','plan')
    with patch('question_progress.time.time',return_value=310):
        p=view(db,'new','running',300)
        assert p['remaining_seconds']==121 and p['estimate_samples']==1
        assert p['percent']==0
    with patch('question_progress.time.time',return_value=350):
        assert view(db,'new','running',300)['estimate_overdue']
        assert view(db,'previous','complete',100)['elapsed_seconds']==131


@pytest.mark.parametrize('params',[{'workspace':['1']},{'workspace':['1'],'date':['2026-10-11']}])
def test_workspace_plan_overlaps_index_preparation_and_preserves_review(tmp_path,params):
    import threading,time
    from graph_questions import GraphQuestions
    gate=threading.Event();entered=threading.Event()
    class Snapshots:
        def request(self,params):return 'rev',object() if gate.is_set() else None,None
        def key(self,params):return 'rev'
        def close(self):pass
    def plan(*args):entered.set();return {'queries':['자료'],'axes':['조건']},False
    def analyze(*args,progress=None,**kw):
        assert kw['plan']['axes']==['조건']
        assert kw['detailed']
        progress('synthesis');progress('review');progress('finalize',cached=False)
        return {'answer':'검토 답변','claims':[{'text':'조건'}],'verification':{'method':'independent_evidence_review'}}
    q=GraphQuestions(tmp_path/'jobs.db',snapshots=Snapshots())
    try:
        with patch('graph_plans.prepare',side_effect=plan),patch('graph_questions.deepen',side_effect=analyze),patch('graph_questions.quick_answer',return_value={'evidence':[{'id':'e'}],'limitations':[]}):
            answer=q.ask('분석해 주세요',params=params)
            assert entered.wait(2) and not gate.is_set()
            identity=answer['job']['id']
            gate.set()
            until=time.monotonic()+3
            while q.active and time.monotonic()<until:time.sleep(.01)
            result=q.get(identity)
            assert result['status']=='complete'
            assert result['progress']['percent']==100
            assert result['result']['verification']['method']=='independent_evidence_review'
    finally:gate.set();q.close()
