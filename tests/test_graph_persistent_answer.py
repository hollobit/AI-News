"""Reusing a completed review requires newly selected, identical full inputs."""
from collections import OrderedDict
from copy import deepcopy
import json
import time

import pytest

from graph_answer_quality import PROFILE
from graph_questions import GraphQuestions
from graph_rag import GraphResult, answer_question
from test_answer_quality import audit


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr('graph_answer_cache._CACHE',OrderedDict())
    calls=[]
    def model(prompt,schema,**kwargs):
        calls.append(kwargs['role'])
        if 'checks' in schema['properties']:
            return audit(axes=('addressed',))
        data=json.loads(prompt.split('\nDATA:\n')[1])
        return dict(claims=[dict(kind='finding',axis='측정 범위',
            evidence_ids=[data['evidence'][0]['id']],passage={'explanation':'실험 성공률은 90%이며 현장 성능은 이 자료로 판단할 수 없습니다.'})],
            paragraphs=[[0]],limitations=[])
    monkeypatch.setattr('semantic.run_structured',model)
    evidence=[dict(id='e',title='벤치마크 결과',text='실험 성공률은 90%다. 현장 성능은 미확인이다.',
                   source_url='https://example.org/a',source_kind='paper',day='2026-10-18')]
    bundle=dict(nodes=[],edges=[],evidence=evidence,coverage={},no_hits=False,limitations=[])
    graph=GraphResult(nodes=[],edges=[],evidence=evidence);graph.search_key='fresh'
    plan={'queries':['벤치마크'],'axes':['측정 범위']}
    return graph,bundle,plan,calls


def run(context,**kwargs):
    graph,bundle,plan,_=context
    return answer_question(graph,'벤치마크 측정 결과는?',retrieval=bundle,analysis_plan=plan,detailed=True,**kwargs)


def test_retained_review_is_copied_without_calls_after_memory_expiry(context,monkeypatch):
    old=run(context);assert len(context[3])==2
    monkeypatch.setattr('graph_answer_cache._CACHE',OrderedDict())
    saved=deepcopy(old);signatures=[]
    def lookup(key):signatures.append(key);return saved
    now=run(context,reuse_lookup=lookup)
    assert signatures==[old['input_signature']]
    assert len(context[3])==2 and now['timing']['answer_cache_source']=='persistent'
    assert not now['timing']['stages']
    assert now['verification']==old['verification'] and now['answer']==old['answer']
    now['claims'][0]['text']='caller mutation'
    assert saved['claims'][0]['text']==old['claims'][0]['text']


@pytest.mark.parametrize('change',['source','uncited','plan','policy','date'])
def test_full_input_changes_require_new_generation_and_review(context,monkeypatch,change):
    old=run(context)
    monkeypatch.setattr('graph_answer_cache._CACHE',OrderedDict())
    if change=='source':context[1]['evidence'][0]['text']+=' 새 조건이 추가됐다.'
    elif change=='uncited':context[1]['evidence'].append(dict(context[1]['evidence'][0],id='counter',text='다른 실험에서 성공률은 30%였다.'))
    elif change=='plan':context[2]['queries'].append('현장 일반화')
    elif change=='policy':monkeypatch.setattr('graph_answer_format.PIPELINE','new-review-policy')
    else:
        from datetime import date,timedelta
        actual=date.today()
        class Tomorrow(date):
            @classmethod
            def today(cls):return actual+timedelta(days=1)
        monkeypatch.setattr('datetime.date',Tomorrow)
    current=run(context,reuse_lookup=lambda _:old)
    assert current['input_signature']!=old['input_signature']
    assert len(context[3])==4 and not current['timing']['answer_cache_hit']


@pytest.mark.parametrize('corrupt',['needs_review','method','policy','citation','missing'])
def test_partial_or_invalid_cached_review_cannot_replace_model_checks(context,monkeypatch,corrupt):
    saved=run(context);monkeypatch.setattr('graph_answer_cache._CACHE',OrderedDict())
    if corrupt=='needs_review':saved['verification']['quality']['status']='needs_review'
    elif corrupt=='method':saved['verification']['method']='self_review'
    elif corrupt=='policy':saved['verification']['quality']['policy']='old-rubric'
    elif corrupt=='citation':saved['claims'][0]['evidence_ids']=['invented']
    else:del saved['answer']
    assert not run(context,reuse_lookup=lambda _:saved)['timing']['answer_cache_hit']
    assert len(context[3])==4


def test_restarted_service_retrieves_again_then_reuses_only_same_inputs(tmp_path,monkeypatch,context):
    graph,bundle,plan,calls=context
    bundle.update(workspace_dependencies={'source':'hash'},workspace_as_of=time.time()-86400)
    monkeypatch.setattr('context_current.current',lambda *a:True)
    composed=[]
    def compose(*a,**kw):composed.append(1);return deepcopy(bundle)
    monkeypatch.setattr('graph_question_context.compose',compose)
    monkeypatch.setattr('graph_plans.prepare',lambda *a,**kw:(plan,True))
    monkeypatch.setattr('decision_tasks.retrieval',lambda *a,**kw:None)
    monkeypatch.setattr('graph_questions.quick_answer',lambda *a:{'evidence':bundle['evidence'],'limitations':[]})
    class Snapshots:
        revision='r1'
        def request(self,params):return self.revision,graph,None
        def key(self,params):return self.revision
        def close(self):pass
    snapshots=Snapshots();path=tmp_path/'jobs.sqlite3'
    request={'question':'벤치마크 측정 결과는?','node_ids':[],'params':{'workspace':['1']}}
    def ask(service):
        first=service.ask(request['question'],params=request['params'])
        until=time.monotonic()+3
        while service.active and time.monotonic()<until:time.sleep(.01)
        result=service.get(first['job']['id']);assert result['status']=='complete',result
        return result
    first_service=GraphQuestions(path,snapshots=snapshots)
    try:
        first=ask(first_service)
        with first_service.db() as db:db.execute('UPDATE graph_question_jobs SET created_at=?',(time.time()-86400,))
    finally:first_service.close()
    monkeypatch.setattr('graph_answer_cache._CACHE',OrderedDict());snapshots.revision='r2'
    second_service=GraphQuestions(path,snapshots=snapshots)
    try:
        second=ask(second_service)
        assert first['id']!=second['id'] and len(composed)==2
        assert len(calls)==2 and second['result']['timing']['answer_cache_source']=='persistent'
        assert second['result']['verification']==first['result']['verification']
        # A retained review still passes through the final current-source gate.
        snapshots.revision='r3'
        monkeypatch.setattr('context_current.current',lambda *a:False)
        pending=second_service.ask(request['question'],params=request['params'])
        until=time.monotonic()+3
        while second_service.active and time.monotonic()<until:time.sleep(.01)
        rejected=second_service.get(pending['job']['id'])
        assert rejected['status']=='stale' and rejected['result']=={}
        assert len(calls)==2
        # Never select a rejected/interrupted row even when it has the same hash.
        with second_service.db() as db:
            db.execute("UPDATE graph_question_jobs SET status='needs_review'")
        assert second_service._retained_answer(request,first['result']['input_signature']) is None
    finally:second_service.close()


@pytest.mark.parametrize('change',['day','selection'])
def test_same_corpus_cannot_take_obsolete_day_or_selection_fast_cache(tmp_path,monkeypatch,change):
    from graph_questions import answer_key
    from graph_evidence_selection import VERSION
    from verified_cache import policy
    class Snapshots:
        def request(self,params):return 'same-revision',None,None
        def key(self,params):return 'same-revision'
        def close(self):pass
    q=GraphQuestions(tmp_path/'day.db',snapshots=Snapshots(),enabled=False)
    request={'question':'최근 벤치마크 현황은?','node_ids':[],'params':{'workspace':['1']}}
    monkeypatch.setattr('graph_questions.answer_day',lambda:'2026-10-18')
    old_key=answer_key('same-revision',request)
    old={'answer':'어제 기준 답변','answer_as_of':'2026-10-18','workspace_dependencies':{'source':'hash'},
         'retrieval':{'policy':VERSION},
         'workspace_as_of':time.time(),'workspace_policy':policy(),
         'verification':{'method':'independent_evidence_review','quality':{'policy':'question-explanation-v1','status':'passed'}}}
    try:
        with q.db() as db:
            db.execute('INSERT INTO graph_question_jobs VALUES (?,?,?,?,?,?,?,?,?)',('yesterday',old_key,'same-revision',json.dumps(request,ensure_ascii=False),'complete',json.dumps(old),'',time.time(),0))
        if change=='day':monkeypatch.setattr('graph_questions.answer_day',lambda:'2026-10-19')
        else:monkeypatch.setattr('graph_evidence_selection.VERSION','new-selection-policy')
        monkeypatch.setattr('graph_questions.cold_answer',lambda *a:None)
        monkeypatch.setattr(q.executor,'submit',lambda *a:None)
        answer=q.ask(request['question'],params=request['params'])
        assert answer['job']['id']!='yesterday' and not answer['timing']['answer_cache_hit']
        assert answer_key('same-revision',request)!=old_key
    finally:q.close()
