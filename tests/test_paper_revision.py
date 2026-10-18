import copy
import json
import sqlite3

import pytest

from paper_analysis import PaperAnalysisService
from paper_revision import apply
from test_paper_realtime import populate


IDENTITY='2610.10000'


class Model:
    def __init__(self):
        self.calls=[];self.accepted=False;self.fail=None

    def __call__(self,prompt,schema):
        data=json.loads(prompt.split('DATA:\n')[1]);self.calls.append((prompt,schema,data))
        stage='review' if prompt.startswith('ROLE: paper_verification') else 'repair' if 'changes' in schema['properties'] else 'draft'
        if self.fail==stage:
            self.fail=None;raise RuntimeError('timeout')
        ref=data['evidence'][0]['id']
        if stage=='review':return dict(accepted=self.accepted,issues=[] if self.accepted else ['실제 적용과 실험 보고를 구분해야 한다.'],checked_evidence_ids=[ref],limitations=['초록 범위'])
        if stage=='repair':
            fixed=copy.deepcopy(data['report']['claims'][1]);fixed['detail']='실험 결과를 보고했으나 실제 적용은 미확인이다.'
            return dict(changes=[dict(claim_index=1,replacement=fixed)],additions=[],summary=None,limitations=None)
        return dict(summary='저자가 보고한 로봇 연구',claims=[dict(category='problem',title='로봇 실험',
            detail='저자가 로봇 실험을 보고했다.',evidence_ids=[ref],uncertainty='초록 범위'),
            dict(category='reported_result',title='적용',detail='실제 적용을 확인했다.',evidence_ids=[ref],uncertainty='초록 범위')],limitations=['초록만 확인했다.'])


@pytest.fixture
def held(tmp_path):
    path=tmp_path/'papers.db';populate(path);model=Model();fetches=[]
    def fetch(url):fetches.append(url);return dict(status='failed')
    service=PaperAnalysisService(path,analyzer=model,enabled=True,start_worker=False,extractor=lambda _:[],fetcher=fetch)
    service.submit([IDENTITY]);service._execute(service._claim())
    assert service.get(IDENTITY)['status']=='needs_review' and len(model.calls)==4
    model.calls.clear()
    yield service,model,fetches
    service.close()


def execute(service):
    service._execute(service._claim());return service.get(IDENTITY)


def test_explicit_retry_uses_feedback_then_full_review_and_preserves_history(held):
    service,model,fetches=held;before=service.get(IDENTITY)['result'];model.accepted=True
    with service.db() as db:
        db.execute('CREATE TABLE paper_pipeline_attempts(paper_id,kind,attempts,at)')
        db.execute("INSERT INTO paper_pipeline_attempts VALUES(?,'analysis',3,123)",(IDENTITY,))
    service.submit([IDENTITY]);result=execute(service)
    assert result['status']=='complete' and len(model.calls)==2 and len(fetches)==1
    assert 'changes' in model.calls[0][1]['properties']
    assert before['verification']['issues'][0] in model.calls[0][0]
    assert model.calls[0][2]['evidence']==model.calls[1][2]['evidence']==before['evidence']
    assert result['result']['report']['claims'][0]==before['report']['claims'][0]
    assert result['result']['revision_history'][:-1]==before['revision_history']
    assert result['result']['revision_history'][-1]['verification']==before['verification']
    with service.db() as db:
        assert db.execute('SELECT attempts FROM paper_pipeline_attempts').fetchone()[0]==3
        assert db.execute('SELECT count(*) FROM paper_analysis_events WHERE stage=?',('review_resume_requested',)).fetchone()[0]==1
        assert json.loads(db.execute('SELECT result_json FROM arxiv_paper_analysis_history').fetchone()[0])==before


@pytest.mark.parametrize('failed_stage,remaining_calls',[('repair',2),('review',1)])
def test_timeout_resumes_exact_stage_without_replenishing_correction_budget(held,failed_stage,remaining_calls):
    service,model,fetches=held
    model.fail=failed_stage;service.submit([IDENTITY]);assert execute(service)['status']=='failed'
    model.calls.clear();service.submit([IDENTITY]);result=execute(service)
    assert result['status']=='needs_review' and len(model.calls)==remaining_calls and len(fetches)==1
    assert len(result['result']['revision_history'])==2
    with service.db() as db:
        state=json.loads(db.execute('SELECT state_json FROM paper_analysis_checkpoints').fetchone()[0])
        assert state['repairs']==1


def test_repeated_queued_submission_does_not_reset_or_duplicate_resume(held):
    service,model,_=held
    service.submit([IDENTITY]);service.submit([IDENTITY]);result=execute(service)
    assert result['status']=='needs_review' and len(model.calls)==2
    with service.db() as db:
        assert db.execute("SELECT count(*) FROM paper_analysis_events WHERE stage='review_resume_requested'").fetchone()[0]==1


def test_legacy_review_is_rechecked_before_any_correction(held):
    service,model,_=held
    with service.db() as db:
        stored=json.loads(db.execute('SELECT result_json FROM arxiv_paper_analyses').fetchone()[0])
        stored['verification'].pop('review_policy')
        db.execute('UPDATE arxiv_paper_analyses SET result_json=?',(json.dumps(stored),))
    service.submit([IDENTITY]);result=execute(service)
    assert result['status']=='needs_review' and len(model.calls)==3
    assert model.calls[0][0].startswith('ROLE: paper_verification')
    assert 'changes' in model.calls[1][1]['properties']
    assert model.calls[2][0].startswith('ROLE: paper_verification')
    assert len(result['result']['revision_history'])==3


def test_new_input_starts_fresh_and_preserves_previous_rejection(held):
    service,model,_=held
    with service.db() as db:
        old=dict(db.execute('SELECT * FROM arxiv_paper_analyses').fetchone())
        metadata=json.loads(db.execute('SELECT metadata_json FROM arxiv_papers').fetchone()[0])
        metadata['abstract']+=' New experimental conditions.'
        db.execute('UPDATE arxiv_papers SET metadata_json=?',(json.dumps(metadata),))
    model.accepted=True;service.submit([IDENTITY]);result=execute(service)
    assert result['status']=='complete' and len(model.calls)==2
    assert 'claims' in model.calls[0][1]['properties']
    assert model.calls[0][2]['report'] is None and result['result']['revision_history']==[]
    assert result['input_hash']!=old['input_hash']
    with service.db() as db:assert db.execute('SELECT result_json FROM arxiv_paper_analysis_history WHERE input_hash=?',(old['input_hash'],)).fetchone()[0]==old['result_json']


def test_checkpoint_mismatch_fails_closed_without_any_model_call(held):
    service,model,_=held;service.submit([IDENTITY])
    with service.db() as db:
        report=json.loads(db.execute('SELECT draft_json FROM arxiv_paper_analyses').fetchone()[0]);report['summary']='체크포인트 밖 변경'
        db.execute('UPDATE arxiv_paper_analyses SET draft_json=?',(json.dumps(report),))
    result=execute(service)
    assert result['status']=='failed' and '체크포인트' in result['error'] and not model.calls


def test_changed_review_policy_does_not_replenish_spent_repair_budget(held):
    service,model,_=held;model.fail='review';service.submit([IDENTITY])
    assert execute(service)['status']=='failed'
    with service.db() as db:
        state=json.loads(db.execute('SELECT state_json FROM paper_analysis_checkpoints').fetchone()[0])
        state['review_policy']='previous-policy'
        db.execute('UPDATE paper_analysis_checkpoints SET state_json=?',(json.dumps(state),))
    model.calls.clear();service.submit([IDENTITY]);result=execute(service)
    assert result['status']=='needs_review' and len(model.calls)==1


def test_shutdown_after_correction_restarts_with_review_only(held):
    service,model,fetches=held;model.accepted=True
    def stop_after_patch(prompt,schema):
        result=model(prompt,schema)
        if 'changes' in schema['properties']:service.close()
        return result
    service.analyzer=stop_after_patch;service.submit([IDENTITY]);assert execute(service)['status']=='paused'
    model.calls.clear()
    resumed=PaperAnalysisService(service.path,analyzer=model,enabled=True,start_worker=False,extractor=lambda _:[],fetcher=lambda _:pytest.fail('No refetch'))
    try:
        result=execute(resumed)
        assert result['status']=='complete' and len(model.calls)==1
        assert model.calls[0][0].startswith('ROLE: paper_verification') and len(fetches)==1
    finally:resumed.close()


@pytest.mark.parametrize('invalid',['index','duplicate','citation','drop_all','summary','limitations'])
def test_partial_output_cannot_bypass_report_and_citation_contract(held,invalid):
    service,_,_=held;result=service.get(IDENTITY)['result'];report=result['report']
    fixed=copy.deepcopy(report['claims'][1])
    patch=dict(changes=[dict(claim_index=1,replacement=fixed)],additions=[],summary=None,limitations=None)
    if invalid=='index':patch['changes'][0]['claim_index']=True
    if invalid=='duplicate':patch['changes']*=2
    if invalid=='citation':fixed['evidence_ids']=['invented']
    if invalid=='drop_all':patch['changes']=[dict(claim_index=i,replacement=None) for i in range(2)]
    if invalid=='summary':patch['summary']=False
    if invalid=='limitations':patch['limitations']=[]
    with pytest.raises(ValueError):apply(report,patch,result['evidence'],[])
