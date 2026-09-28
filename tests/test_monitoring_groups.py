import sqlite3
from unittest.mock import patch
import pytest
from monitoring_groups import merge_signals,family_for,filter_group
from dynamic_registry import save_manual,set_excluded,list_registry
from dynamic_strategy import apply_dynamic_trends
from strategy_trends import trend_metrics


def signal(identity,label,observations):
    current={doc for doc,day in observations if day>='2026-09-10'}
    previous={doc for doc,day in observations if day<'2026-09-10'}
    return dict(id=identity,label=label,name=label,terms=[label],current=len(current),previous=len(previous),
        origin='builtin' if identity=='kill_switch' else 'automatic',
        _observations=[dict(document_id=doc,day=day,observation_type='reported_mention') for doc,day in observations],
        evidence=[dict(document_id=doc,day=day,quote=label,origin='text') for doc,day in observations][-1:])


def test_full_document_union_not_sample_count_or_sum():
    a=signal('kill_switch','비상정지',[(str(i),'2026-09-10') for i in range(20)]+[('0','2026-09-03')])
    b=signal('dynamic-signal:safe','안전장치',[('0','2026-09-10'),('20','2026-09-10'),('0','2026-09-11')])
    group=merge_signals([a,b],'2026-09-10','2026-09-16')[0]
    assert (group['current'],group['previous'])==(21,1)
    assert group['series']==[1,0,0,0,0,0,0,21,1,0,0,0,0,0]
    assert group['observation_types']['reported_mention']==21
    assert len(group['evidence'])==1 # only current sample exists, counts still complete
    assert group['members'][0]['series'][7]==20
    assert group['days'][0]=='2026-09-03' and group['days'][-1]=='2026-09-16'


def test_separate_controls_unknown_stability_and_empty_hidden():
    assert family_for(dict(id='training_pause',name='학습 일시중단'))[0]!='signal-group:deployment'
    assert family_for(dict(id='a',label='새로운 관측'))==family_for(dict(id='b',label='새로운 관측'))
    a=signal('deployment_stop','배포 중단',[])
    b=signal('auto','컴패니언 서비스 중단',[('a','2026-09-10')])
    groups=merge_signals([a,b],'2026-09-10','2026-09-16')
    assert len(groups)==1 and groups[0]['current']==1
    assert groups[0]['member_ids']==['deployment_stop','auto']
    assert len(groups[0]['members'])==1
    assert merge_signals([a],'2026-09-10','2026-09-16')==[]


def test_group_filter_uses_original_guards_and_honors_exclusion():
    with sqlite3.connect(':memory:') as db:
        entry=save_manual(db,{'kind':'signal','label':'안전장치','terms':['안전장치']})
        items=[dict(message_id=i,title='뉴스',text=text,day='2026-09-16',source_url=f'https://example.org/{i}') for i,text in enumerate([
            'AI kill switch 도입을 제안했다.','정부가 AI 안전장치 규제를 강화했다.',
            'AI가 자동차를 소개했다. 자동차 kill switch를 고쳤다.','AI 모델을 발표했다.'])]
        found=filter_group(db,items,'signal-group:safeguards',{})
        assert [i['message_id'] for i in found]==[0,1]
        set_excluded(db,entry['id'],True)
        assert [i['message_id'] for i in filter_group(db,items,'signal-group:safeguards',{})]==[0]
        with pytest.raises(ValueError):filter_group(db,items,'signal-group:missing',{})


def test_projection_groups_fixed_and_manual_without_persisting_membership():
    with sqlite3.connect(':memory:') as db:
        save_manual(db,{'kind':'signal','label':'안전장치','terms':['안전장치']})
        items=[dict(message_id=i,title='뉴스',text=text,day='2026-09-16',source_url=f'https://example.org/{i}') for i,text in enumerate([
            '정부가 AI kill switch와 안전장치 규제를 강화했다.',
            '정부가 AI 안전장치 규제를 강화했다.'])]
        with patch('dynamic_cooccurrence.build_cooccurrence_candidates',return_value=[]):
            result=apply_dynamic_trends(db,items,{},trend_metrics(db,items,{}),None)
        group=result['monitoring']['topics'][0]
        assert group['current']==2 and len(group['members'])==2
        assert sum(m['current'] for m in group['members'])==3
        assert all('_observations' not in m['metadata'] for m in list_registry(db)['items'])
        assert all('_observations' not in s for s in result['dynamic_signal_lenses'])
