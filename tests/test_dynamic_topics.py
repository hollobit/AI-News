import hashlib
import sqlite3
from keyword_index import keyword_record_id
from morphology import item_text
from dynamic_topics import build_dynamic_topics,match_dynamic_item
from dynamic_registry import save_manual,set_excluded,list_registry


def corpus(text='정부가 AI 접근 심사를 의무화했다.',label='접근 심사',count=3):
    items=[]; morph={}
    kid=hashlib.sha256(label.encode()).hexdigest()[:16]
    for i in range(count):
        item={'chat_id':1,'message_id':i,'item_index':0,'title':'AI 정책 변화','text':text,
              'day':f'2026-09-{12+i:02}','source_url':f'https://example.org/{i}','topic':'policy'}
        full=item_text(item); start=full.find(label)
        terms=[] if start<0 else [{'id':kid,'label':label,'surface':label,'start':start,'end':start+len(label),'kind':'noun_phrase','pos':['NNG','NNG']}]
        items.append(item); morph[keyword_record_id(item)]=terms
    return items,morph,kid


def test_dynamic_registration_stability_source_quotes_and_dormancy():
    db=sqlite3.connect(':memory:'); items,morph,kid=corpus()
    # "심사" is a governance cue through the explicit surrounding regulation.
    for item in items:item['text']='정부가 AI 접근 심사 규제를 의무화했다.'
    items,morph,kid=corpus('정부가 AI 접근 심사 규제를 의무화했다.')
    first=build_dynamic_topics(db,items,morph)
    topic=next(t for t in first['topics'] if t['keyword_id']==kid)
    assert topic['id']=='dynamic:'+kid and topic['current']==3 and topic['status']=='emerging'
    assert topic['verification']=='rule_checked' and topic['strategic_multiplier']>1
    assert len(first['signals'])==1
    for evidence in topic['evidence']:
        item=next(i for i in items if i['source_url']==evidence['url'])
        assert item['text'][evidence['start']:evidence['end']]==evidence['quote']
    again=build_dynamic_topics(db,items,morph)
    assert again['topics'][0]['version']==topic['version']
    changed=[dict(item,text='정부가 AI 정책을 발표했다.') for item in items]
    empty=build_dynamic_topics(db,changed,{})
    assert empty['topics'][0]['status']=='dormant' and empty['topics'][0]['evidence']==[]
    assert empty['topics'][0]['version']>topic['version']
    db.close()


def test_duplicate_urls_and_unrelated_words_do_not_promote():
    items,morph,kid=corpus()
    for item in items:item['source_url']='https://example.org/same'
    assert build_dynamic_topics(None,items,morph)['topics']==[]
    items,morph,_=corpus('맛집에서 계절 요리를 소개했다.','계절 요리')
    for item in items:item.update(title='생활 소식',topic='general')
    # Rebuild exact offsets after changing titles.
    for item in items:
        term=morph[keyword_record_id(item)][0]; term['start']=item_text(item).index('계절 요리');term['end']=term['start']+5
    assert build_dynamic_topics(None,items,morph)['topics']==[]


def test_signal_same_sentence_and_learning_rate_exclusion():
    items,morph,kid=corpus('정부가 AI 정책을 강화했다.\n접근 심사 자료를 공개했다.')
    assert build_dynamic_topics(None,items,morph)['signals']==[]
    items,morph,kid=corpus('정부가 AI 학습률 규제를 의무화했다.','학습률')
    assert build_dynamic_topics(None,items,morph)['signals']==[]


def test_manual_zero_hits_exclusion_and_matching():
    db=sqlite3.connect(':memory:')
    manual=save_manual(db,{'kind':'topic','label':'추론 허가','terms':['추론 허가'],'description':'관찰'})
    items,morph,kid=corpus('정부가 AI 접근 심사 규제를 의무화했다.')
    output=build_dynamic_topics(db,items,morph)
    manual_dto=next(t for t in output['topics'] if t['id']==manual['id'])
    assert manual_dto['zero_hits'] and manual_dto['terms']==['추론 허가']
    auto=next(t for t in output['topics'] if t['id']=='dynamic:'+kid)
    assert match_dynamic_item(items[0],auto,morph[keyword_record_id(items[0])])
    signal=output['signals'][0]
    assert match_dynamic_item(items[0],signal,morph[keyword_record_id(items[0])])
    set_excluded(db,auto['id'],True)
    after=build_dynamic_topics(db,items,morph)
    assert not any(t['id']==auto['id'] for t in after['topics'])
    assert next(row for row in list_registry(db)['items'] if row['id']==auto['id'])['excluded']
    db.close()


def test_signal_can_use_later_exact_occurrence_after_neutral_title():
    items,morph,kid=corpus('정부가 AI 접근 심사 규제를 의무화했다.')
    for item in items:
        item['title']='접근 심사 자료'
        morph[keyword_record_id(item)][0].update(start=0,end=5)
    result=build_dynamic_topics(None,items,morph)
    assert result['signals'][0]['current']==3
    assert all(e['origin']=='telegram_excerpt' for e in result['signals'][0]['evidence'])


def test_share_normalized_growth_and_cooling():
    items,morph,kid=corpus('정부가 AI 접근 심사 규제를 의무화했다.',count=3)
    for i,item in enumerate(items):item['day']=f'2026-09-{2+i:02}'
    current,records,_=corpus('정부가 AI 접근 심사 규제를 의무화했다.',count=3)
    for i,item in enumerate(current):
        old=keyword_record_id(item); item['message_id']+=10; item['source_url']+='-new'
        morph[keyword_record_id(item)]=records[old]
    previous_background=[dict(item,message_id=20+i,title='기타',text='다른 소식',source_url=item['source_url']+'-background') for i,item in enumerate(items)]
    growth=build_dynamic_topics(None,items+current+previous_background,morph)['topics'][0]
    assert growth['status']=='growing' and growth['current']==3 and growth['previous']==3 and growth['share_change_pp']==50
    current_background=[dict(item,message_id=30+i,title='기타',text='다른 소식',source_url=item['source_url']+'-background') for i,item in enumerate(current)]
    cooling=build_dynamic_topics(None,items+current+current_background,morph)['topics'][0]
    assert cooling['status']=='cooling' and cooling['share_change_pp']==-50


def test_entity_role_mentions_are_not_control_signals():
    for label,kind in [('남자친구','proper_noun'),('알리바바','proper_noun'),('에이전트','technical_dictionary'),('공급망','technical_dictionary')]:
        items,morph,kid=corpus(f'정부가 AI {label} 규제를 의무화했다.',label)
        for terms in morph.values():terms[0]['kind']=kind
        result=build_dynamic_topics(None,items,morph)
        assert result['signals']==[]
        if label=='남자친구':assert result['topics']==[]
    items,morph,kid=corpus('정부가 AI 컴패니언 서비스 중단 정책을 시행했다.','컴패니언 서비스 중단')
    assert build_dynamic_topics(None,items,morph)['signals'][0]['label']=='컴패니언 서비스 중단'


def test_existing_low_rank_topic_is_not_dropped_by_new_registration_cap():
    from dynamic_registry import sync_automatic
    db=sqlite3.connect(':memory:')
    # An existing, currently unsupported topic remains explicit zero evidence,
    # rather than silently disappearing when new candidates fill the window.
    sync_automatic(db,[{'id':'dynamic:older','kind':'topic','label':'기존 기술 주제','terms':['기존 기술 주제'],
                       'metadata':{'keyword_id':'older','kind':'topics','label':'기존 기술 주제','term_kind':'noun_phrase'}}])
    items,morph,kid=corpus()
    terms=[]
    for i in range(165):
        label=f'검증 체계 {i}'
        terms.append(label)
    text='정부 AI 정책: '+', '.join(terms)
    for item in items:
        item['text']=text
        full=item_text(item)
        morph[keyword_record_id(item)]=[{'id':'new'+str(i),'label':label,'surface':label,'start':full.index(label),'end':full.index(label)+len(label),'kind':'noun_phrase','pos':['NNG']} for i,label in enumerate(terms)]
    result=build_dynamic_topics(db,items,morph)
    assert any(t['id']=='dynamic:older' and t['zero_hits'] for t in result['topics'])
    assert result['omitted_new_count']==5
    version=list_registry(db)['version']
    again=build_dynamic_topics(db,items,morph)
    assert list_registry(db)['version']==version
    assert {t['id'] for t in again['topics']}=={t['id'] for t in result['topics']}
    db.close()


def test_commercial_subscription_pause_without_governance_is_not_signal():
    items,morph,kid=corpus('AI 모델 출시 후 수요 폭증으로 신규 구독 중단을 발표했다.','신규 구독 중단')
    assert build_dynamic_topics(None,items,morph)['signals']==[]


def test_proposals_and_negated_actions_are_explicit_observation_types():
    for sentence,expected in [('정부가 AI 접근 심사 규제 도입을 제안했다.','proposed'),('정부가 AI 접근 심사 규제를 의무화하지 않는다.','negated_or_conditional')]:
        items,morph,kid=corpus(sentence)
        signal=build_dynamic_topics(None,items,morph)['signals'][0]
        assert signal['observation_types'][expected]==3
        assert sum(signal['observation_types'].values())==3
        assert all(e['observation_type']==expected and e['caution'] for e in signal['evidence'])
    items,morph,kid=corpus('AI 프롬프트 인젝션 공격 우회를 탐지했다.','프롬프트 인젝션')
    assert build_dynamic_topics(None,items,morph)['signals'][0]['label']=='프롬프트 인젝션'


def test_manual_override_of_automatic_id_is_not_duplicated_or_reactivated():
    db=sqlite3.connect(':memory:');items,morph,kid=corpus('정부가 AI 접근 심사 규제를 의무화했다.')
    first=build_dynamic_topics(db,items,morph)
    original=first['topics'][0]
    save_manual(db,{'id':original['id'],'kind':'topic','label':'수동 변경 주제','terms':['아직 없는 명사구'],'description':'수동 우선'})
    result=build_dynamic_topics(db,items,morph)
    matching=[topic for topic in result['topics'] if topic['id']==original['id']]
    assert len(matching)==1 and matching[0]['label']=='수동 변경 주제' and matching[0]['current']==0
    db.close()


def test_exact_builtin_alias_links_do_not_create_duplicate_cards():
    db=sqlite3.connect(':memory:')
    items,morph,kid=corpus('정부가 AI 비상정지 안전 정책을 도입했다.','비상정지')
    for terms in morph.values():terms[0]['kind']='technical_dictionary'
    result=build_dynamic_topics(db,items,morph)
    assert not any(t['keyword_id']==kid for t in result['topics']+result['signals'])
    links=[entry for entry in result['builtin_alias_links'] if entry['keyword_id']==kid]
    assert links and all(entry['builtin_ids']==['kill_switch'] for entry in links)
    set_excluded(db,'kill_switch',True)
    excluded=build_dynamic_topics(db,items,morph)
    assert not any(t['keyword_id']==kid for t in excluded['topics']+excluded['signals'])
    assert all(entry['excluded_builtin_ids']==['kill_switch'] for entry in excluded['builtin_alias_links'])
    manual=save_manual(db,{'kind':'topic','label':'비상정지','terms':['비상정지'],'description':'별도 수동 관측'})
    restored=build_dynamic_topics(db,items,morph)
    assert any(t['id']==manual['id'] and t['current']==3 for t in restored['topics'])
    db.close()


def test_builtin_alias_normalization_is_exact_not_fuzzy():
    for label,expected in [('Physical AI',False),('physical   AI',False),('Physical AI 안전 감사',True)]:
        items,morph,kid=corpus(f'정부가 AI {label} 정책을 강화했다.',label)
        result=build_dynamic_topics(None,items,morph)
        assert any(t['keyword_id']==kid for t in result['topics']) is expected
