from datetime import datetime,timedelta,timezone
from strategic_workflow import WorkflowService
from test_workflow_efficiency import Model,Sources,wait,ComplexModel,run
import copy
import json
import pytest


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


@pytest.mark.parametrize('reject', [False,True])
def test_failed_source_ordinary_excerpt_keeps_review_and_failure_scope(tmp_path,reject):
    calls=[];inputs=[];model=Model(reject=reject)
    class FailedSource:
        def fetch(self,url,refresh=False):
            calls.append(refresh)
            return dict(status='failed',url=url,text='',next_retry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    def analyze(prompt,schema):
        inputs.append(json.loads(prompt.split('DATA:\n')[1]))
        return model(prompt,schema)
    service=WorkflowService(tmp_path/'db',sources=FailedSource(),analyzer=analyze,enabled=True)
    try:
        result=run(service)
        assert calls==[False]  # Never bypass provider cooldown to qualify for the route.
        assert model.calls[:2]==['integrated_analysis','integrated_verification']
        assert len(model.calls)==(5 if reject else 2)
        assert result['status']==('needs_review' if reject else 'complete')
        assert inputs[0]['coverage']['failed_urls']==['https://example.com/a']
        assert result['results']['coverage']['failed_urls']==['https://example.com/a']
        assert {e['origin'] for e in result['results']['evidence']}=={'telegram_excerpt'}
        assert result['artifacts']['execution_plan']['reason']=='available_news_excerpt'
        assert result['results']['orchestration']['independent_role_reports'] is False
        assert result['results']['verified'] is (not reject)
    finally:service.close()


@pytest.mark.parametrize('text', ['환자 임상 결과', '평가 데이터 오염', '에이전트 통제 우회',
    'Agent bypassed its controls', 'mixed findings', 'contradictory evidence',
    '벤치마크 진단 정확도', '논문: Breast cancer diagnosis', '보안 벤치마크',
    '벤치마크를 둘러싼 소송', 'benchmark security dispute', 'benchmark lawsuit'])
def test_failed_source_never_overrides_sensitive_or_disputed_evidence_exclusions(text):
    from workflow_routing import select
    e=dict(id='e',text=text,origin='telegram_excerpt')
    enrichment=dict(evidence=[e],coverage={'failed_urls':['https://example.com/a']})
    assert select([e],dict(analysis_mode='adaptive-v2'),enrichment)['path']=='multi-role'


@pytest.mark.parametrize('text,path', [('AI 도구 공개','compact-v1'),('벤치마크 실험 결과','parallel-drafts-v1')])
def test_failed_source_keeps_existing_role_checkpoint_and_other_scope_guards(text,path):
    from workflow_routing import select
    e=dict(id='e',text=text,origin='telegram_excerpt')
    request=dict(analysis_mode='adaptive-v2');enrichment=dict(evidence=[e],coverage={'failed_urls':['https://example.com/a']})
    previous=dict(path='multi-role',reason='source_unavailable',content_route='simple_document',routing_version='adaptive-v6')
    saved=copy.deepcopy(previous)
    assert select([e],request,enrichment,previous=previous)==previous==saved
    assert select([e],request,enrichment)['path']==path
    for extra in (dict(completion_attempt=2),dict(question='사용자 추가 질문'),dict(active_rules={'rules':['rule']}),
                  dict(improvement_context={'review_issues':['근거 누락']})):
        assert select([e],dict(request,**extra),enrichment)['path']=='multi-role'
    assert select([e,e],request,enrichment)['path']=='multi-role'
    assert select([e],request,dict(enrichment,evidence=[dict(e,text='')]))['path']=='multi-role'
    assert select([e],request,dict(enrichment,evidence=[dict(e,origin='external_source')]))['path']=='multi-role'
    assert select([e],request,dict(enrichment,evidence=[dict(e,origin='fetched_url_excerpt')]))['path']=='multi-role'
    assert select([e],request,dict(enrichment,evidence=[dict(e,text=text+'x'*14000)]))['path']=='multi-role'


def test_partial_complex_guard_checks_source_metadata_and_retains_full_source_route():
    from workflow_routing import select
    e=dict(id='e',text='벤치마크 실험 결과',origin='telegram_excerpt')
    request=dict(analysis_mode='adaptive-v2')
    for metadata in (dict(url='https://pubmed.ncbi.nlm.nih.gov/1'),dict(title='진단 정확도 비교')):
        evidence=dict(e,**metadata)
        enrichment=dict(evidence=[evidence],coverage={'failed_urls':[evidence.get('url','https://example.com')]})
        assert select([evidence],request,enrichment)['path']=='multi-role'
        # The conservative new admission guard does not reroute existing
        # successful-source work or make an empty excerpt sufficient.
        enrichment['coverage']['failed_urls']=[]
        assert select([evidence],request,enrichment)['path']=='parallel-drafts-v1'


@pytest.mark.parametrize('failed', [False,True])
@pytest.mark.parametrize('text', ['Agent bypassed its controls','mixed findings','평가 결과 상충','에이전트 통제 우회'])
def test_shared_content_exclusions_cannot_enter_either_short_route(failed,text):
    from workflow_routing import select
    e=dict(id='e',origin='telegram_excerpt',text=text)
    enrichment=dict(evidence=[e],coverage={'failed_urls':['https://example.com'] if failed else []})
    assert select([e],dict(analysis_mode='adaptive-v2'),enrichment)['path']=='multi-role'


@pytest.mark.parametrize('text', ['Breast cancer diagnosis','진단 결과 소개','새 보안 도구 공개'])
def test_partial_medical_security_scope_cannot_fall_through_to_compact(text):
    from workflow_routing import select
    e=dict(id='e',origin='telegram_excerpt',text=text)
    enrichment=dict(evidence=[e],coverage={'failed_urls':['https://example.com']})
    assert select([e],dict(analysis_mode='adaptive-v2'),enrichment)['path']=='multi-role'


@pytest.mark.parametrize('outcome', ['accepted','repaired','rejected'])
def test_partial_complex_parallel_drafts_keep_originals_and_independent_gates(tmp_path,outcome):
    calls=[];inputs=[];model=ComplexModel(repair=outcome=='repaired')
    model.reject=outcome=='rejected'
    class FailedSource:
        def fetch(self,url,refresh=False):
            calls.append(refresh)
            return dict(status='failed',url=url,text='',next_retry=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
    def analyze(prompt,schema):
        inputs.append((prompt.splitlines()[0][6:],json.loads(prompt.split('DATA:\n')[1])))
        return model(prompt,schema)
    service=WorkflowService(tmp_path/'db',sources=FailedSource(),analyzer=analyze,enabled=True)
    try:
        created=service.create_run([dict(title='벤치마크 도구 공개',text='기업이 새 AI 개발 도구를 공개했다.',source_url='https://example.com/a')],
                                   dict(analysis_mode='adaptive-v2',completion=True))
        result=wait(service,created['id']);final=result['results']
        assert result['status']==('needs_review' if outcome=='rejected' else 'complete'),result['error']
        assert calls==[False]
        assert set(model.calls[:2])=={'strategy_draft','risk_assessment'}
        assert model.calls[2]=='integrated_verification'
        assert len(model.calls)=={'accepted':3,'repaired':5,'rejected':6}[outcome]
        for role,data in inputs:
            assert data['evidence']==final['evidence']
            if role in ('strategy_draft','risk_assessment'):
                assert data['context']['coverage']==final['coverage']
            elif role=='integrated_verification':
                assert data['coverage']==final['coverage']
        assert final['coverage']['failed_urls']==['https://example.com/a']
        assert {e['origin'] for e in final['evidence']}=={'telegram_excerpt'}
        assert result['artifacts']['execution_plan']['path']=='parallel-drafts-v1'
        assert result['artifacts']['execution_plan']['reason']=='available_news_excerpt'
        assert final['orchestration']['normal_path_calls']==3
        assert final['orchestration']['separate_risk_generation'] is True
        from graph_rag import validated_workflow_content
        from risk_analysis import validated_risk_content
        assert bool(validated_workflow_content(final,result['id']))==(outcome!='rejected')
        assert bool(validated_risk_content(final,result['id']))==(outcome!='rejected')
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
