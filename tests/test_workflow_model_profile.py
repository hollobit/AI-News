import json
import pytest
from model_policy import policy


def prompt(text='기업이 새 도구를 공개했다.',**extra):
    data=dict(evidence=[dict(id='news_'+'a'*24,text=text,origin='telegram_excerpt')],coverage={'failed_urls':[]})
    data.update(extra)
    return 'ROLE: integrated_analysis\nDATA:\n'+json.dumps(data,ensure_ascii=False)


def test_only_short_factual_generation_uses_luna_medium():
    p=prompt()
    assert policy('integrated_analysis',prompt=p)['model']=='gpt-6-luna'
    assert policy('integrated_analysis',prompt=p)['reasoning_effort']=='medium'
    assert policy('integrated_verification',prompt=p)['model']=='gpt-6.1-sol'
    assert policy('risk_verification',prompt=p,escalation=True)['model']=='gpt-6-astra'
    assert policy('integrated_analysis',prompt=p,escalation=True)['model']=='gpt-6-astra'
    assert policy('baseline_analysis')['reasoning_effort']=='high'


@pytest.mark.parametrize('text',['환자 진단 도구 공개','공개 성능 99%','벤치마크 공개','통제 우회 기법 공개','발표된 연구의 상충 근거','취약점 공개','도구 공개 '+('길다'*500)])
def test_complex_sensitive_and_long_inputs_keep_sol(text):
    assert policy('integrated_analysis',prompt=prompt(text))['model']=='gpt-6.1-sol'


def test_failed_sources_other_contracts_and_multiple_articles_keep_sol():
    p=prompt(coverage={'failed_urls':['https://example.com']})
    assert policy('integrated_analysis',prompt=p)['tier']=='sol'
    for origins in [['external_source'],['telegram_excerpt','telegram_excerpt']]:
        evidence=[dict(id=str(i),text='도구 공개',origin=o) for i,o in enumerate(origins)]
        assert policy('integrated_analysis',prompt=prompt(evidence=evidence))['tier']=='sol'
    assert policy('integrated_analysis',prompt='bad JSON')['tier']=='sol'


def test_profile_changes_cache_policy_only_for_selected_work():
    assert policy('integrated_analysis',prompt=prompt())['policy_version']=='news-models-announcement-v1'
    assert policy('integrated_analysis',prompt=prompt('성능 평가 결과'))['policy_version']=='news-models-v1'
    assert policy('risk_patch')['tier']=='sol'  # Field-patch evaluation did not justify rollout.
