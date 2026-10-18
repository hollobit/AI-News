import copy
import json
import threading
from unittest.mock import patch

import pytest

from strategic_workflow import WorkflowService
from workflow_synthesis import VERSION, eligible
from test_strategic_workflow import Model
from test_workflow_efficiency import wait


REQUEST=dict(analysis_mode='adaptive-v2',completion=True,synthesis_schedule=VERSION)
ITEM=dict(title='보안 취약점 검증',text='취약점 검증은 실험 조건에 한정된다.',source_url='https://example.org/security')


class Sources:
    text='취약점 검증은 실험 조건에 한정되며 현장 적용 결과는 미확인이다.'
    def fetch(self,url):
        return dict(status='fetched',url=url,text=self.text,fetched_at='2026-10-18T00:00:00+00:00')


class ReviewedModel(Model):
    def __init__(self,barrier=None,reject_difference=False):
        super().__init__();self.barrier=barrier;self.reject_difference=reject_difference
        self.finished=set();self.reviewed=[]

    def __call__(self,prompt,schema):
        role=prompt.splitlines()[0][6:]
        data=json.loads(prompt.split('DATA:\n')[1]);context=data['context']
        if self.barrier and role in ('national','technology','risk_assessment','synthesis'):
            self.barrier.wait(2)
        if role=='synthesis':
            assert not {'analysts','deliberation','risk_report'} & context.keys()
            assert '다른 역할 보고서가 도착하기 전에' in prompt
        value=super().__call__(prompt,schema)
        if role in ('national','technology'):
            value['claims'][0]['detail']='공공 도입의 조건은 미확인' if role=='national' else '현장 효과는 미확인'
        if role in ('synthesis','revision'):value['event_observations']=[]
        if role=='verification':
            assert {'national','technology','synthesis'} <= self.finished
            self.reviewed.append(data)
            value.update(checked_event_indices=[],deliberation_checks=[dict(
                difference_id=q['id'],material=True,classification='different_scope',
                preserved=not self.reject_difference,reason='현재 원문과 모든 역할 조건 대조',
                opposing_evidence_ids=[],change_conditions=[])
                for q in context['deliberation']['questions']])
        self.finished.add(role)
        return value


def service(tmp_path,model,sources=None):
    instance=WorkflowService(tmp_path/'news.db',sources=sources or Sources(),analyzer=model,
                             extractor=lambda _:[],enabled=True)
    instance._retrieve_graph=lambda *_:{'status':'complete','result':{}}
    return instance


def test_all_four_generations_overlap_but_review_waits_for_actual_role_positions(tmp_path):
    model=ReviewedModel(threading.Barrier(4));workflow=service(tmp_path,model)
    try:
        # Isolate scheduling from hash-stripe contention, as other concurrency tests do.
        with patch('workflow_efficiency.cached_call',side_effect=lambda s,r,stage,prompt,schema,validate,**kw:validate(s._analyze(prompt,schema))):
            run=workflow.create_run([ITEM],REQUEST);result=wait(workflow,run['id'])
        assert result['status']=='complete',result['error']
        assert len(model.calls)==6
        assert result['artifacts']['execution_plan']['path']=='multi-role'
        assert result['results']['orchestration']['synthesis_schedule']==VERSION
        checked=model.reviewed[0]
        assert {p['role'] for p in checked['context']['deliberation']['positions']}=={'national','technology'}
        assert checked['evidence']==result['results']['evidence']
        assert result['results']['verification']['deliberation_checks'][0]['preserved']
        assert result['results']['risk_verified']
    finally:workflow.close()


def test_missing_material_difference_cannot_pass_even_if_reviewer_says_accepted(tmp_path):
    model=ReviewedModel(reject_difference=True);workflow=service(tmp_path,model)
    try:
        result=wait(workflow,workflow.create_run([ITEM],REQUEST)['id'])
        assert result['status']=='needs_review',result['error']
        assert not result['artifacts']['verification']['accepted']
        assert not result['results']['verified']
        assert any('중요 역할 이견' in issue for issue in result['results']['verification']['issues'])
        assert model.calls.count('synthesis')==1 and model.calls.count('revision')==1
        assert model.calls.count('verification')==2
        assert model.calls.count('risk_verification')==1
    finally:workflow.close()


def test_strategy_review_can_finish_while_risk_generation_is_still_running(tmp_path):
    reviewed=threading.Event()
    class SlowRisk(ReviewedModel):
        def __call__(self,prompt,schema):
            role=prompt.splitlines()[0][6:]
            if role=='risk_assessment':assert reviewed.wait(2)
            value=super().__call__(prompt,schema)
            if role=='verification':
                assert 'risk_assessment' not in self.finished
                reviewed.set()
            return value
    model=SlowRisk();workflow=service(tmp_path,model)
    try:
        with patch('workflow_efficiency.cached_call',side_effect=lambda s,r,stage,prompt,schema,validate,**kw:validate(s._analyze(prompt,schema))):
            result=wait(workflow,workflow.create_run([ITEM],REQUEST)['id'])
        assert result['status']=='complete',result['error']
        assert result['results']['verified'] and result['results']['risk_verified']
        assert len(model.calls)==6 and 'risk_verification' in model.finished
    finally:workflow.close()


def test_identical_early_draft_reuses_exact_checkpoint_and_changed_source_does_not(tmp_path):
    model=ReviewedModel();sources=Sources();workflow=service(tmp_path,model,sources)
    try:
        first=wait(workflow,workflow.create_run([ITEM],REQUEST)['id'])
        assert first['status']=='complete',first['error']
        calls=len(model.calls)
        second=wait(workflow,workflow.create_run([ITEM],REQUEST)['id'])
        assert second['status']=='complete' and len(model.calls)==calls
        assert 'reuse_synthesis' in second['artifacts']
        sources.text+=' 새로운 실험 제약이 확인됐다.'
        third=wait(workflow,workflow.create_run([ITEM],REQUEST)['id'])
        assert third['status']=='complete' and model.calls.count('synthesis')==2
        assert third['results']['verification']['evidence_hash']!=first['results']['verification']['evidence_hash']
    finally:workflow.close()


@pytest.mark.parametrize('change',['legacy','manual','mode','question','terms','retry','issues','followup','rules','many','failed_source','foreign_source','oversized'])
def test_new_schedule_keeps_old_requests_custom_scope_and_retries_unchanged(change):
    request=copy.deepcopy(REQUEST)
    evidence=[dict(id='one',origin='telegram_excerpt',text='취약점 실험 조건')]
    snapshot=list(evidence);enrichment=dict(evidence=evidence,coverage={'failed_urls':[]})
    assert eligible(snapshot,request,enrichment)
    if change=='legacy':request.pop('synthesis_schedule')
    elif change=='manual':request['completion']=False
    elif change=='mode':request['analysis_mode']='legacy'
    elif change in ('question','terms'):request[change]='별도 질문'
    elif change=='retry':request['completion_attempt']=2
    elif change=='issues':request['improvement_context']={'review_issues':['중요 반론 보존']}
    elif change=='followup':request['improvement_context']={'followup_tasks':[{'text':'후속 확인'}]}
    elif change=='rules':request['active_rules']={'rules':['custom']}
    elif change=='many':snapshot.append(evidence[0])
    elif change=='failed_source':enrichment['coverage']['failed_urls']=['https://example.org/failed']
    elif change=='foreign_source':evidence[0]['origin']='generated_opinion'
    elif change=='oversized':evidence[0]['text']='x'*8001
    assert not eligible(snapshot,request,enrichment)
