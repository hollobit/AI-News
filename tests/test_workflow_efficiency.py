import copy
import json
import time
import pytest
from strategic_workflow import WorkflowService
from workflow_compact import eligible
from baseline_citations import candidates, materialize


class Sources:
    def fetch(self, url):
        return dict(status='fetched', url=url, text='기업이 새 AI 개발 도구를 공개했다.', fetched_at='2026-09-30T00:00:00+00:00')


class Model:
    def __init__(self, reject=False, omit=False):
        self.calls=[]; self.reject=reject; self.omit=omit
    def __call__(self, prompt, schema):
        role=prompt.splitlines()[0][6:];self.calls.append(role)
        data=json.loads(prompt.split('DATA:\n')[1]);ids=[e['id'] for e in data['evidence']]
        if role=='integrated_analysis':
            return dict(report=dict(summary='개발 도구 공개', claims=[dict(title='도구 공개', detail='기업이 AI 도구를 공개했다.', category='watch_signal', evidence_ids=[ids[0]], uncertainty='사용 성과는 미확인')], limitations=['성과 미확인'], event_observations=[]), risk_report=dict(summary='위험 판단 근거 부족',risks=[],assessed_evidence_ids=[],not_assessable_evidence_ids=ids,limitations=['피해 근거 없음']))
        audit=dict(accepted=not self.reject,issues=['근거 부족'] if self.reject else [],checked_evidence_ids=[] if self.omit else ids,limitations=[])
        return dict(verification=dict(audit,checked_event_indices=[]),risk_verification=audit)


def wait(service, identity):
    for _ in range(300):
        result=service.get_run(identity)
        if not service.active:return service.get_run(identity)
        time.sleep(.01)
    pytest.fail('workflow did not finish')


def run(service, text='기업이 새 AI 개발 도구를 공개했다.'):
    r=service.create_run([dict(title=text,text=text,source_url='https://example.com/a')],dict(analysis_mode='compact-v1',completion=True))
    return wait(service,r['id'])


@pytest.mark.parametrize('reject,omit',[(False,False),(True,False),(False,True)])
def test_two_calls_keep_independent_risk_and_strategy_gates(tmp_path,reject,omit):
    m=Model(reject,omit);s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=m,enabled=True)
    try:
        result=run(s)
        assert m.calls==['integrated_analysis','integrated_verification']
        assert result['status']==('needs_review' if reject or omit else 'complete'),result['error']
        from graph_rag import validated_workflow_content
        assert bool(validated_workflow_content(result['results'],result['id'])) == (not reject and not omit)
        assert 'deliberation' not in result['results']  # Do not fabricate independent role debate.
    finally:s.close()


def test_identical_inputs_reuse_but_changed_evidence_regenerates(tmp_path):
    m=Model();s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=m,enabled=True)
    try:
        first=run(s);assert first['status']=='complete'
        second=run(s);assert second['status']=='complete'
        assert len(m.calls)==2
        assert 'reuse_integrated_analysis' in second['artifacts']
        third=run(s,'기업이 새 AI 개발 도구를 공개하고 사용 범위를 변경했다.')
        assert third['status']=='complete'
        assert len(m.calls)==4
    finally:s.close()


def test_rejected_review_is_not_cached_but_draft_can_be_reused(tmp_path):
    m=Model(reject=True);s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=m,enabled=True)
    try:
        assert run(s)['status']=='needs_review'
        m.reject=False
        assert run(s)['status']=='complete'
        assert m.calls==['integrated_analysis','integrated_verification','integrated_verification']
    finally:s.close()


def test_sensitive_retry_multi_document_and_active_rules_keep_legacy_path():
    e={'id':'a','text':'AI 도구 공개','origin':'telegram_excerpt'};context={'evidence':[e],'coverage':{'failed_urls':[]}}
    request={'analysis_mode':'compact-v1'}
    assert eligible([e],request,context)
    assert not eligible([e],request,{'evidence':[dict(e,origin='external_source')],'coverage':{}})
    assert not eligible([e,e],request,context)
    assert not eligible([e],dict(request,question='별도 분석 질문'),context)
    assert not eligible([e],dict(request,improvement_context={'followup_tasks':[{'text':'후속 검토'}]}),context)
    assert not eligible([e],dict(request,completion_attempt=2),context)
    assert not eligible([e],dict(request,active_rules={'rules':['rule']}),context)
    assert not eligible([e],request,{'evidence':[dict(e,text='환자 임상 결과')],'coverage':{}})
    assert not eligible([e],request,{'evidence':[dict(e,text='벤치마크 결과와 평가 데이터 오염 위험')],'coverage':{}})


def test_keyword_ids_materialize_only_current_document_verbatim_surface():
    snap={'evidence':[{'text':'소버린AI와 GPU를 소개했다.'}]}
    prepared={'keywords':[{'label':'소버린 AI','surface':'소버린AI'},{'label':'PU','surface':'PU'}]}
    allowed=candidates(snap,prepared)
    assert list(allowed.values())==[{'label':'소버린 AI','source_quote':'소버린AI'}]
    r=materialize({'keywords':[{'citation_id':next(iter(allowed))}]},allowed)
    assert r['keywords']==list(allowed.values())
    with pytest.raises(ValueError):materialize({'keywords':[{'citation_id':'foreign'}]},allowed)


def test_stage_dependency_change_invalidates_only_changed_stage(tmp_path):
    s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=Model(),enabled=True)
    try:
        calls=[]
        def step(name):
            calls.append(name);return {'value':name}
        s._stage('run','first',lambda:step('first'),dependencies={'input':1})
        s._stage('run','second',lambda:step('second'),dependencies={'input':1})
        s._stage('run','first',lambda:step('wrong'),dependencies={'input':1})
        s._stage('run','second',lambda:step('changed'),dependencies={'input':2})
        assert calls==['first','second','changed']
    finally:s.close()


def test_model_policy_change_invalidates_cache(tmp_path,monkeypatch):
    import workflow_efficiency
    m=Model();s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=m,enabled=True)
    try:
        assert run(s)['status']=='complete'
        original=workflow_efficiency.policy
        monkeypatch.setattr(workflow_efficiency,'policy',lambda *a,**k:dict(original(*a,**k),policy_version='new-policy'))
        assert run(s)['status']=='complete'
        assert len(m.calls)==4
    finally:s.close()


@pytest.mark.parametrize('text,expected',[
    ('논문 검색 도구를 공개했다. 이용자는 제목으로 문서를 찾을 수 있다.', 'simple_publication_announcement'),
    ('새 모델의 기술 논문과 코드를 공개했다.', 'simple_publication_announcement'),
    ('논문 공개. 벤치마크 평가 데이터 오염으로 모델 선택이 왜곡될 수 있다.', 'complex_evidence'),
    ('새 논문 공개: 모델의 실험 성능이 향상됐다.', 'research_findings_or_unclear'),
    ('Paper published on arxiv: experiments show improved accuracy.', 'research_findings_or_unclear'),
    ('논문 공개: 환자 치료 임상 결과', 'sensitive'),
    ('논문 내용을 소개한다.', 'research_findings_or_unclear'),
])
def test_announcement_routing_preserves_findings_and_risk_exclusions(text,expected):
    from workflow_compact import content_route
    assert content_route([{'text':text}]) == expected


def test_fetched_findings_override_short_announcement():
    from workflow_compact import content_route
    assert content_route([{'text':'논문과 코드를 공개했다.'}, {'text':'실험 결과 정확도가 향상됐다.'}]) == 'complex_evidence'
