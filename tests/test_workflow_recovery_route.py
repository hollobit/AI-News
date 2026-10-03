import copy
import pytest
from workflow_recovery_route import ADMISSION_ISSUES,operational_feedback_only
from workflow_routing import select


def request():
    issues=sorted(ADMISSION_ISSUES)
    return dict(completion=True,completion_attempt=1,analysis_mode='adaptive-v2',improvement_context={
        'review_issues':issues,'followup_tasks':[dict(kind='completion_review',text='\n'.join(issues))]})


def test_exact_operational_feedback_uses_three_calls_without_erasing_history():
    r=request();prior=copy.deepcopy(r)
    e=dict(evidence=[dict(id='e',origin='telegram_excerpt',text='벤치마크 실험 결과 및 조건')],coverage={})
    assert operational_feedback_only(r)
    assert select([e['evidence'][0]],r,e)['path']=='parallel-drafts-v1'
    assert r==prior


@pytest.mark.parametrize('text',['평가 데이터 오염','conflicting findings','환자 임상 실험','에이전트 통제 우회','unauthorized access'])
def test_operational_feedback_never_overrides_content_exclusions(text):
    e=dict(evidence=[dict(id='e',origin='telegram_excerpt',text=text)],coverage={})
    assert select([e['evidence'][0]],request(),e)['path']=='multi-role'


def test_unknown_review_or_custom_task_retry_source_failure_keep_full_path():
    e=dict(evidence=[dict(id='e',origin='telegram_excerpt',text='일반 연구 비교 결과')],coverage={})
    for mutate in (
        lambda r:r['improvement_context']['review_issues'].append('위험 시나리오 누락'),
        lambda r:r['improvement_context']['followup_tasks'].append(dict(kind='custom',text='추가 질문')),
        lambda r:r.update(completion_attempt=2),
        lambda r:r.update(question='별도 질문'),
    ):
        r=request();mutate(r)
        assert select([e['evidence'][0]],r,e)['path']=='multi-role'
    e['coverage']['failed_urls']=['https://example.com']
    assert select([e['evidence'][0]],request(),e)['path']=='multi-role'
