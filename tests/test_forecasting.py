from copy import deepcopy
import pytest
from forecasting import VERSION,validate,run
from strategic_jobs import schemas,validate_report


def snapshot():
 return dict(kind='forecast',forecast_version=VERSION,as_of='2026-10-11',horizon_days=180,subjects=[{'id':'s'}],evidence=[dict(id='a',url='https://example.com/a'),dict(id='b',url='https://example.com/b')])


def report():
 return dict(summary='조건부 전망',comparisons=[dict(subject_id='s',title='관측 가능한 사건',assessment='미래의 조건부 해석',assumptions=['가정'],evidence_ids=['a','b'],probability_percent=50,probability_lower=25,probability_upper=75,window_start='2026-11-01',window_end='2027-01-01',method='subjective',probability_basis='기준 빈도 자료 없음; 가정에 따른 추정',resolution_criterion='공식 발표로 판정',counter_evidence=['지연 가능성'],update_signals=['공식 일정 변경'])],limitations=['통계적으로 보정되지 않음'])


def test_valid_forecast_routes_through_existing_validation():
 validate_report(report(),snapshot())
 assert 'probability_percent' in schemas(snapshot())[0]['properties']['comparisons']['items']['properties']


@pytest.mark.parametrize('changes',[
 {'probability_percent':False},{'probability_percent':101},{'probability_percent':51},
 {'probability_lower':55},{'probability_lower':45,'probability_upper':55},
 {'evidence_ids':['a']},{'evidence_ids':['invented']},
 {'window_start':'2026-10-01'},{'window_end':'2028-01-01'},
 {'window_start':None},{'method':'calibrated'},{'resolution_criterion':''},
 {'counter_evidence':[]},{'method':'unavailable'},
])
def test_invalid_numeric_claims_are_rejected(changes):
 value=report();value['comparisons'][0].update(changes)
 with pytest.raises((ValueError,TypeError)):validate(value,snapshot())


def test_duplicate_urls_do_not_count_as_independent_sources():
 snap=snapshot();snap['evidence'][1]['url']=snap['evidence'][0]['url']
 with pytest.raises(ValueError):validate(report(),snap)


def test_abstention_preserves_unknown_instead_of_zero():
 value=report();value['comparisons'][0].update(method='unavailable',probability_percent=None,probability_lower=None,probability_upper=None,window_start=None,window_end=None,evidence_ids=['a'])
 validate(value,snapshot())


def test_independent_review_rejection_keeps_prediction_unverified():
 calls=[]
 def analyzer(prompt,schema,**kw):
  calls.append(kw['role'])
  return report() if len(calls)==1 else dict(accepted=False,issues=['시기 근거 부족'],checked_evidence_ids=['a','b'],assumptions_separated=True,limitations=[])
 result=run(snapshot(),analyzer,{})
 assert not result['verified']
 assert calls==['strategic_forecast','strategic_forecast_verification']
 assert result['snapshot_hash'] and result['evidence_hash']
