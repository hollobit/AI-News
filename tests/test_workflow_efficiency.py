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


def test_keyword_candidate_must_not_split_hyphenated_model_name():
    allowed=candidates({'evidence':[{'text':'SMCL-DTA 모델'}]}, {'keywords':[{'label':'SMCL','surface':'SMCL'},{'label':'SMCL-DTA','surface':'SMCL-DTA'}]})
    assert list(allowed.values())==[{'label':'SMCL-DTA','source_quote':'SMCL-DTA'}]


def test_keyword_candidate_korean_suffix_is_not_a_particle():
    from baseline_citations import complete_surface
    assert not complete_surface('출국 통제 법제','출국 통제 법제화를 시행했다.')
    assert complete_surface('출국 통제 법제화','출국 통제 법제화를 시행했다.')
    assert complete_surface('소버린AI','소버린AI와 GPU')
    assert not complete_surface('국','출국 통제')


class ComplexModel(Model):
    def __init__(self,repair=False):
        super().__init__();self.repair=repair;self.reviews=0
    def __call__(self,prompt,schema):
        role=prompt.splitlines()[0][6:]
        if role in ('strategy_draft','risk_assessment'):
            self.calls.append(role)
            value=Model()(prompt.replace('ROLE: '+role,'ROLE: integrated_analysis',1),schema)
            return value['report' if role=='strategy_draft' else 'risk_report']
        if role=='strategy_patch':
            self.calls.append(role)
            data=json.loads(prompt.split('DATA:\n')[1]);identity,name=data['fields'][0]
            return {'patches':[{'target_id':identity,'field':name,'value':'원문에 따르면 새 AI 도구를 공개했다.'}]}
        value=super().__call__(prompt,schema)
        if role=='integrated_verification':
            self.reviews+=1
            if self.repair and self.reviews==1:
                data=json.loads(prompt.split('DATA:\n')[1])
                identity=next(k for k in data['strategy_target_map'] if k.startswith('claim_'))
                value['verification'].update(accepted=False,issues=['설명 보완'],revision_targets=[{'issue_index':0,'target_id':identity,'fields':['detail']}])
        return value


@pytest.mark.parametrize('repair',[False,True])
def test_complex_three_calls_and_targeted_repair_keep_public_gates(tmp_path,repair):
    m=ComplexModel(repair);s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=m,enabled=True)
    try:
        r=s.create_run([dict(title='벤치마크 연구',text='벤치마크 연구 도구를 공개했다.',source_url='https://example.com/a')],dict(analysis_mode='adaptive-v2',completion=True))
        result=wait(s,r['id'])
        assert result['status']=='complete',result['error']
        assert result['results']['orchestration']['pattern']=='parallel-drafts-v1'
        assert len(m.calls)==(5 if repair else 3)
        assert m.calls.count('risk_assessment')==1
        from graph_rag import validated_workflow_content
        from risk_analysis import validated_risk_content
        assert validated_workflow_content(result['results'],r['id'])
        assert validated_risk_content(result['results'],r['id'])
        if repair:assert 'revision_patch_plan' in result['artifacts']
    finally:s.close()


def test_patch_scope_is_complete_and_immutable():
    from workflow_patch import plan,targets,apply
    report={'summary':'unchanged','claims':[{'detail':'old','title':'same'}],'limitations':[]}
    identity=next(k for k in targets(report) if k.startswith('claim_'))
    audit={'issues':['wrong detail'],'revision_targets':[{'issue_index':0,'target_id':identity,'fields':['detail']}]}
    fields=plan(report,audit)
    updated=apply(report,fields,{'patches':[{'target_id':identity,'field':'detail','value':'new'}]})
    assert report['claims'][0]['detail']=='old'
    assert updated['summary']=='unchanged' and updated['claims'][0]['title']=='same'
    assert plan(report,dict(audit,issues=['wrong detail','global omission'])) is None
    with pytest.raises(ValueError):apply(report,fields,{'patches':[{'target_id':'report','field':'summary','value':'bad'}]})
    with pytest.raises(ValueError):apply(report,fields,{'patches':[]})


def test_complex_path_excludes_observed_contamination_regression():
    from workflow_complex import eligible
    e={'id':'news_a','origin':'telegram_excerpt','text':'벤치마크 결과는 data contamination에 취약하고 작업별 순위가 바뀐다.'}
    assert not eligible([e],{'analysis_mode':'adaptive-v2'},{'evidence':[e],'coverage':{}})
    assert not eligible([e],{'analysis_mode':'adaptive-v2'},{'evidence':[dict(e,text='에이전트가 통제를 우회한 사건')],'coverage':{}})


def test_complex_high_risk_review_keeps_astra_escalation(tmp_path):
    from test_risk_analysis import fixture
    class HighRisk(ComplexModel):
        def __call__(self,prompt,schema):
            result=super().__call__(prompt,schema)
            if prompt.startswith('ROLE: risk_assessment'):
                data=json.loads(prompt.split('DATA:\n')[1]);ids=[e['id'] for e in data['evidence']]
                result=fixture()['risk_report'];result['risks'][0]['evidence_ids']=ids
                result['assessed_evidence_ids']=ids
            return result
    s=WorkflowService(tmp_path/'db',sources=Sources(),analyzer=HighRisk(),enabled=True)
    selected=[];original=s._validated_call
    def traced(stage,*args,**kwargs):
        selected.append((stage,kwargs.get('escalation',False)))
        return original(stage,*args,**kwargs)
    s._validated_call=traced
    try:
        r=s.create_run([dict(title='벤치마크 연구',text='새 연구 도구 공개',source_url='https://example.com/a')],dict(analysis_mode='adaptive-v2',completion=True))
        result=wait(s,r['id'])
        assert result['status']=='complete',result['error']
        assert ('integrated_verification',True) in selected
    finally:s.close()


def test_risk_patch_enforces_original_output_bounds_before_review():
    from test_risk_analysis import fixture
    from workflow_patch import targets,apply
    report=fixture()['risk_report'];identity=next(k for k in targets(report,True) if k.startswith('risk_'))
    with pytest.raises(ValueError,match='120자'):
        apply(report,[(identity,'current_basis')],{'patches':[{'target_id':identity,'field':'current_basis','value':'x'*121}]},True)
    with pytest.raises(ValueError,match='배열'):
        apply(report,[(identity,'observed_indicators')],{'patches':[{'target_id':identity,'field':'observed_indicators','value':['a','b','c']}]},True)


def test_concurrent_identical_workflows_share_generation_and_review(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    model=Model()
    services=[WorkflowService(tmp_path/'shared.db',sources=Sources(),analyzer=model,enabled=True,recover_interrupted=False) for _ in range(2)]
    try:
        with ThreadPoolExecutor(2) as pool:
            futures=[pool.submit(s.create_run,[{'title':'도구 공개','text':'기업이 새 AI 개발 도구를 공개했다.','source_url':'https://example.org/tool'}],{'analysis_mode':'adaptive-v2'}) for s in services]
            runs=[f.result() for f in futures]
        results=[wait(s,r['id']) for s,r in zip(services,runs)]
        assert all(r['status']=='complete' for r in results)
        assert model.calls.count('integrated_analysis')==1
        assert model.calls.count('integrated_verification')==1
        with services[0].db() as db:
            assert db.execute("select count(*) from workflow_cache_observations where outcome='hit'").fetchone()[0]==2
    finally:
        for s in services:s.close()
