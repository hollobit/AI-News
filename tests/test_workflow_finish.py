import json
import threading
import unittest
from unittest.mock import patch
from test_strategic_workflow import WorkflowTests as Helpers, Model


class FinishTests(unittest.TestCase):
    setUp=Helpers.setUp
    tearDown=Helpers.tearDown
    service=Helpers.service
    items=Helpers.items
    done=Helpers.done
    def test_risk_review_starts_while_analysts_are_busy(self):
        reviewed=threading.Event();model=Model()
        def analyzer(prompt,schema):
            role=prompt.splitlines()[0][6:]
            if role=='risk_verification':reviewed.set()
            if role in ('national','technology'):assert reviewed.wait(2)
            return model(prompt,schema)
        service=self.service(analyzer)
        # This test isolates the dependency graph from unrelated cache lock stripes.
        with patch('workflow_efficiency.cached_call',side_effect=lambda s,r,stage,prompt,schema,validate,**kw:validate(s._analyze(prompt,schema))):
            result=self.done(service,service.create_run(self.items())['id'])
        assert result['status']=='complete',result['error']

    def test_strategy_review_does_not_wait_for_risk_review(self):
        strategy_reviewed=threading.Event();model=Model()
        def analyzer(prompt,schema):
            role=prompt.splitlines()[0][6:]
            if role=='verification':strategy_reviewed.set()
            if role=='risk_verification':assert strategy_reviewed.wait(2)
            return model(prompt,schema)
        service=self.service(analyzer)
        with patch('workflow_efficiency.cached_call',side_effect=lambda s,r,stage,prompt,schema,validate,**kw:validate(s._analyze(prompt,schema))):
            result=self.done(service,service.create_run(self.items())['id'])
        assert result['status']=='complete',result['error']

    def test_revisions_overlap_and_keep_rejected_reviews(self):
        barrier=threading.Barrier(2);model=Model(reject=True)
        def analyzer(prompt,schema):
            role=prompt.splitlines()[0][6:]
            if role in ('revision','risk_revision'):barrier.wait(2)
            return model(prompt,schema)
        service=self.service(analyzer)
        with patch('workflow_efficiency.cached_call',side_effect=lambda s,r,stage,prompt,schema,validate,**kw:validate(s._analyze(prompt,schema))):
            result=self.done(service,service.create_run(self.items())['id'])
        assert result['status']=='needs_review',result['error']
        assert not result['results']['verified']
        assert 'risk_reverification' in result['artifacts']
        assert 'reverification' in result['artifacts']

    def test_risk_generation_failure_does_not_hang_draft_future(self):
        model=Model()
        def analyzer(prompt,schema):
            if prompt.startswith('ROLE: risk_assessment'):raise ValueError('risk failed')
            return model(prompt,schema)
        service=self.service(analyzer)
        result=self.done(service,service.create_run(self.items())['id'])
        assert result['status']=='failed'
        assert 'risk failed' in result['error']


def test_note_schema_preserves_claim_fields_and_validation():
    from workflow_notes import schema,normalize
    from strategic_workflow import REPORT
    assert set(schema()['properties'])=={'claims','limitations'}
    assert schema()['properties']['claims']==REPORT['properties']['claims']
    notes={'claims':[{'detail':'상충 근거 보존'}],'limitations':['관측 불확실성']}
    normalized=normalize(notes)
    assert normalized['summary']==''
    assert normalized['claims']==notes['claims']
    assert 'summary' not in notes

# The imported fixture class is not another test suite.
del Helpers


def test_parallel_route_repairs_both_reports_before_joint_review(tmp_path):
    from test_workflow_efficiency import ComplexModel,Sources,wait
    from strategic_workflow import WorkflowService
    class Rejected(ComplexModel):
        def __call__(self,prompt,schema):
            result=super().__call__(prompt,schema)
            if prompt.startswith('ROLE: integrated_verification') and self.reviews==1:
                for key in ('verification','risk_verification'):
                    result[key].update(accepted=False,issues=['정확한 설명 필요'])
            return result
    service=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=Rejected(),enabled=True)
    barrier=threading.Barrier(2);repaired=[]
    def revise(stage,evidence,report,*args,**kwargs):
        barrier.wait(2);repaired.append(stage);return report
    try:
        with patch.object(service,'_revise',side_effect=revise):
            run=service.create_run([dict(title='벤치마크 연구',source_url='https://example.com/a')],dict(analysis_mode='adaptive-v2',completion=True))
            result=wait(service,run['id'])
        assert set(repaired)=={'revision','risk_revision'}
        assert result['status']=='complete',result['error']
        assert not result['results']['orchestration']['initial_audits']['verification']['accepted']
    finally:service.close()
