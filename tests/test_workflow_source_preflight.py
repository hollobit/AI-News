from datetime import datetime,timedelta,timezone
from strategic_workflow import WorkflowService
from test_workflow_efficiency import Model,Sources,wait,ComplexModel,run


def test_source_repair_precedes_analysis_and_survives_resume(tmp_path):
    events=[]
    class RecoveringSource(Sources):
        def fetch(self,url,refresh=False):
            events.append('refresh' if refresh else 'fetch')
            if not refresh:return dict(status='failed',url=url,text='',error='temporary')
            return super().fetch(url)
    model=Model();reviews=0
    def analyze(prompt,schema):
        nonlocal reviews
        role=prompt.splitlines()[0][6:];events.append(role)
        if role=='integrated_verification':
            reviews+=1
            if reviews==1:raise TimeoutError('transient')
        return model(prompt,schema)
    service=WorkflowService(tmp_path/'db',sources=RecoveringSource(),analyzer=analyze,enabled=True)
    try:
        first=run(service)
        assert first['status']=='failed'
        assert events==['fetch','refresh','integrated_analysis','integrated_verification']
        resumed=service.resume(first['id']);final=wait(service,resumed['id'])
        assert final['status']=='complete',final['error']
        assert events==['fetch','refresh','integrated_analysis','integrated_verification','integrated_verification']
        assert final['results']['coverage']['failed_urls']==[]
    finally:service.close()


def test_source_provider_cooldown_prevents_forced_refresh(tmp_path):
    calls=[]
    class WaitingSource:
        def fetch(self,url,refresh=False):
            calls.append(refresh)
            return dict(status='failed',url=url,text='',next_retry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    service=WorkflowService(tmp_path/'db',sources=WaitingSource(),analyzer=Model(),enabled=True)
    try:
        evidence=[dict(id='a',url='https://example.com/a',text='원문',origin='telegram_excerpt')]
        initial=service._enrich(evidence)
        retry=service._enrich(evidence,retry=initial)
        assert calls==[False] and retry['coverage']['failed_urls']==['https://example.com/a']
    finally:service.close()


def test_compact_repairs_only_rejected_claim_then_reviews_both_reports(tmp_path):
    model=ComplexModel(repair=True)
    service=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=model,enabled=True)
    try:
        result=run(service)
        assert result['status']=='complete',result['error']
        assert model.calls==['integrated_analysis','integrated_verification','strategy_patch','integrated_verification']
        assert result['results']['orchestration']['max_repair_cycles']==1
        assert not result['results']['orchestration']['initial_audits']['verification']['accepted']
    finally:service.close()
