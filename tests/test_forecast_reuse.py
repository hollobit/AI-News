from copy import deepcopy
import json
import threading
import pytest
from forecasting import run, reusable_draft
from strategic_jobs import StrategicJobs
from test_forecasting import snapshot, report


def audit():
    return dict(accepted=True,issues=[],checked_evidence_ids=['a','b'],assumptions_separated=True,limitations=[])


def test_transport_failure_checkpoints_draft_and_new_job_reviews_only(tmp_path):
    calls=[]
    def analyze(prompt,schema,role):
        calls.append(role)
        if role=='strategic_forecast':return report()
        if calls.count(role)==1:raise TimeoutError('private')
        return audit()
    service=StrategicJobs(tmp_path/'jobs',analyzer=analyze,enabled=True)
    try:
        first=service.start(snapshot())['id'];service.futures[first].result(3)
        assert service.get(first)['status']=='failed'
        assert 'report' not in service.get(first)['result']
        with service.db() as db:
            saved=json.loads(db.execute('SELECT result_json FROM intel_analysis_jobs WHERE id=?',(first,)).fetchone()[0])
        assert reusable_draft(snapshot(),saved)
        second=service.start(snapshot())['id'];service.futures[second].result(3)
        value=service.get(second)
        assert value['status']=='complete' and value['result']['resumed_from']==first
        assert value['progress']['stages'][1]['reused']
        assert calls==['strategic_forecast','strategic_forecast_verification','strategic_forecast_verification']
        assert service.get(first)['status']=='failed'  # Preserve history.
    finally:service.close()


@pytest.mark.parametrize('change',['source','policy','draft'])
def test_checkpoint_requires_exact_snapshot_policy_and_draft(change,monkeypatch):
    state={};calls=[]
    def analyze(prompt,schema,role):
        calls.append(role)
        if role=='strategic_forecast':return report()
        raise TimeoutError()
    with pytest.raises(TimeoutError):run(snapshot(),analyze,state)
    snap=snapshot()
    if change=='source':snap['evidence'][0]['version']=2
    if change=='policy':monkeypatch.setattr('forecasting.CHECKPOINT_VERSION','changed')
    if change=='draft':state['report']['summary']='unreviewed change'
    assert not reusable_draft(snap,state)
    with pytest.raises(TimeoutError):run(snap,analyze,state)
    assert calls.count('strategic_forecast')==2


def test_rejected_forecast_is_not_treated_as_failed_transport(tmp_path):
    calls=[]
    def analyze(prompt,schema,role):
        calls.append(role)
        return report() if role=='strategic_forecast' else dict(audit(),accepted=False,issues=['시기 근거 부족'])
    service=StrategicJobs(tmp_path/'jobs',analyzer=analyze,enabled=True)
    try:
        for _ in range(2):
            identity=service.start(snapshot())['id'];service.futures[identity].result(3)
            assert service.get(identity)['status']=='needs_review'
        assert calls.count('strategic_forecast')==2
    finally:service.close()


def test_two_forecasts_can_generate_concurrently(tmp_path):
    barrier=threading.Barrier(2)
    def analyze(prompt,schema,role):
        if role=='strategic_forecast':barrier.wait(3);return report()
        return audit()
    service=StrategicJobs(tmp_path/'jobs',analyzer=analyze,enabled=True)
    try:
        first=service.start(snapshot())['id']
        second=service.start(dict(snapshot(),question='별도 질문'))['id']
        for identity in (first,second):
            service.futures[identity].result(4)
            assert service.get(identity)['status']=='complete'
    finally:service.close()


def test_changed_source_stops_resume_before_model_call(tmp_path):
    calls=[]
    current=deepcopy(snapshot()['evidence']);current[0]['source_hash']='changed'
    service=StrategicJobs(tmp_path/'jobs',analyzer=lambda *a,**k:calls.append(k),enabled=True,current_evidence=lambda ids:current)
    try:
        identity=service.start(snapshot())['id'];service.futures[identity].result(3)
        assert service.get(identity)['status']=='failed' and not calls
    finally:service.close()
