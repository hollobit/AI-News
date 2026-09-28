from datetime import date,timedelta
from observatory import build_projection
from keyword_index import keyword_record_id,document_id

DAYS=[(date(2026,9,3)+timedelta(days=i)).isoformat() for i in range(14)]

def test_full_document_index_exceeds_samples_and_edges_intersect_same_date():
    items=[];morph={}
    for day in DAYS[-2:]:
        for i in range(12):
            item=dict(chat_id=1,message_id=len(items),item_index=0,day=day,title=f'문서 {i}',text='비공개 발췌',source_url=f'https://example.org/{i}')
            items.append(item)
            terms=['a','b'] if i<8 else (['a'] if day==DAYS[-2] else ['b'])
            morph[keyword_record_id(item)]=[dict(id=k,label=k,kind='noun_phrase') for k in terms]
    result=build_projection(items,morph,[],{},DAYS)
    assert result['document_index_version']==1
    a,b=result['nodes']
    assert len(a['evidence_by_day'][-2])<=3
    assert max(map(len,a['document_ids_by_day']))==12
    ids=lambda n:set(sum(n['document_ids_by_day'],[]))
    edge_ids=ids(a)&ids(b)
    assert len({result['documents'][i]['document_id'] for i in edge_ids})==8
    assert result['edges'][0]['count']==8
    assert len({result['documents'][i]['document_id'] for i in ids(a)})==a['count']==12
    assert all('excerpt' not in d and 'text' not in d for d in result['documents'].values())

def fixture():
    items=[];morph={}
    for i,(url,day,terms) in enumerate([
        ('a',DAYS[0],['a']),('a',DAYS[1],['b']), # same URL on different dates is NOT a cooccurrence
        ('b',DAYS[7],['a','b']),('c',DAYS[7],['a','b']),
        ('b',DAYS[7],['a','b']), # repeated message, one document
        ('b',DAYS[8],['a','b']), # daily count repeats, period union does not
        ('old','2026-08-01',['a','b'])]):
        item=dict(message_id=i,chat_id=1,item_index=0,title='실제 제목',text='원문 발췌',day=day,source_url='https://example.org/'+url)
        items.append(item);morph[keyword_record_id(item)]=[dict(id=k,label=k,kind='noun_phrase') for k in terms]
    return items,morph


def test_edges_join_same_date_and_deduplicate_period_and_daily():
    items,morph=fixture();result=build_projection(items,morph,[],{},DAYS)
    assert len(result['nodes'])==2 and len(result['edges'])==1
    edge=result['edges'][0]
    assert edge['count']==2 and edge['series'][7:9]==[2,1]
    assert edge['series'][:7]==[0]*7
    assert edge['previous']==0 and edge['current']==2
    for e in result['evidence'].values():assert e['day'] in DAYS and e['url'].startswith('https://example.org/')


def test_topic_membership_restricts_links_and_keeps_stable_ids():
    items,morph=fixture();topic={'id':'signal-group:safe','label':'안전 주제','grouped':True}
    membership={topic['id']:{(document_id(i),i['day']) for i in items if i['day']==DAYS[7]}}
    a=build_projection(items,morph,[topic],membership,DAYS)
    b=build_projection(list(reversed(items)),morph,[topic],membership,DAYS)
    ta=next(n for n in a['nodes'] if n['kind']=='topic')
    assert ta['current']==2 and ta['series'][8]==0
    assert len(a['edges'])==3
    assert {e['id'] for e in a['edges']}=={e['id'] for e in b['edges']}
    assert all(e['series'][8]==0 for e in a['edges'] if topic['id'] in (e['source'],e['target']))


def test_empty_and_bounded_projection():
    empty=build_projection([],{},[],{},DAYS)
    assert not empty['nodes'] and not empty['edges'] and not empty['evidence']
    items,morph=fixture()
    for item in items:morph[keyword_record_id(item)]=[dict(id=str(k),label=str(k),kind='noun_phrase') for k in range(60)]
    result=build_projection(items,morph,[],{},DAYS)
    assert len(result['nodes'])==48 and len(result['edges'])==180
    assert result['limits']['eligible_edges']>180
    assert all(len(day)<=3 for row in result['nodes']+result['edges'] for day in row['evidence_by_day'])


def test_expanded_projection_accepts_larger_display_budget():
    items,morph=fixture()
    for item in items:
        morph[keyword_record_id(item)]=[dict(id=str(k),label=str(k),kind='noun_phrase') for k in range(60)]
    result=build_projection(items,morph,[],{},DAYS,keyword_limit=60,edge_limit=360)
    assert len(result['nodes'])==60
    assert result['limits']['keywords']==60 and result['limits']['edges']==360


def test_unchanged_poll_bypasses_heavy_views_and_registry_change_refreshes(tmp_path):
    import sqlite3
    from unittest.mock import patch
    from observatory import read_observatory
    with sqlite3.connect(tmp_path/'cache.sqlite') as db:
        registry={'version':1,'items':[]}
        with patch('projection_cache.revision_token',return_value=('source',1)), \
             patch('dynamic_registry.list_registry',side_effect=lambda db:dict(registry)), \
             patch('strategy_views.dataset',return_value={'items':[],'morph':{}}) as dataset, \
             patch('observatory.candidate_context',return_value={'end':DAYS[-1],'lenses':[],'monitoring':{'topics':[]}}) as overview:
            a=read_observatory(db);b=read_observatory(db)
            assert a['version']==b['version']
            assert dataset.call_count==overview.call_count==1
            registry['version']=2
            registry['items'].append({'id':'manual:cache','kind':'topic','origin':'manual','terms':['changed'],'label':'changed','excluded':True})
            read_observatory(db)
            assert overview.call_count==2


def test_edge_budget_keeps_rare_topic_and_keyword_relationships():
    items=[];morph={}
    for i in range(30):
        item=dict(chat_id=1,message_id=i,item_index=0,title='기사',text='원문',day=DAYS[7],source_url=f'https://example.org/{i}')
        items.append(item);morph[keyword_record_id(item)]=[dict(id=str(k),label=str(k),kind='noun_phrase') for k in range(24)]
    topics=[dict(id='topic:'+str(i),label=str(i)) for i in range(11)]
    all_docs={(document_id(i),i['day']) for i in items}
    members={t['id']:all_docs for t in topics}
    members[topics[-1]['id']]={(document_id(i),i['day']) for i in items[:2]}
    graph=build_projection(items,morph,topics,members,DAYS)
    assert len(graph['edges'])==180
    assert sum(topics[-1]['id'] in (e['source'],e['target']) for e in graph['edges'])>=3
    assert sum(e['source'].startswith('keyword:') and e['target'].startswith('keyword:') for e in graph['edges'])>=20


def test_topic_selection_balances_change_and_volume_without_empty_topics():
    from observatory import select_topics,MAX_TOPICS
    lenses=[dict(id=f'dynamic:{i}',name=str(i),current=100+i,previous=100,dynamic=True) for i in range(50)]
    lenses += [dict(id='rare-rise',current=4,previous=1,dynamic=True),dict(id='rare-fall',current=1,previous=10,dynamic=True),dict(id='empty',current=0,previous=10),dict(id='no-comparison',current=10,previous=0),dict(id='builtin',current=2,previous=2)]
    selected=select_topics({'lenses':lenses,'monitoring':{'topics':[]}})
    ids={t['id'] for t in selected}
    assert len(selected)==MAX_TOPICS
    assert {'rare-rise','rare-fall','builtin'}<=ids
    assert 'empty' not in ids
    assert 'no-comparison' in ids


def test_long_windows_keep_week_comparisons_fixed():
    days=[(date(2026,6,19)+timedelta(days=i)).isoformat() for i in range(90)]
    items=[];morph={}
    for i in (0,76,82,83,89):
        item=dict(chat_id=1,message_id=i,item_index=0,day=days[i],title='기사',text='원문',source_url=f'https://example.org/{i}')
        items.append(item);morph[keyword_record_id(item)]=[dict(id='a',label='반도체',kind='noun_phrase')]
    result=build_projection(items,morph,[],{},days)
    n=result['nodes'][0]
    assert len(n['series'])==90 and n['count']==5
    assert n['previous']==2 and n['current']==2
    assert '90일' in result['method']


def test_processing_history_uses_stable_real_event_ids(tmp_path):
    import sqlite3
    from observatory_runtime import processing_events
    with sqlite3.connect(tmp_path/'events.db') as db:
        for table in ('bulk_baseline_events','strategic_workflow_events'):
            db.execute(f'CREATE TABLE {table}(seq INTEGER PRIMARY KEY,run_id,stage,detail,created_at)')
            db.execute(f'INSERT INTO {table} VALUES (1,?,?,?,?)',('run','verification','실제 검토 기록','2026-09-16T10:00:00Z'))
        a=processing_events(db);b=processing_events(db)
        assert a==b and len(a['events'])==2
        assert len({e['id'] for e in a['events']})==2
        assert all(e['detail']=='실제 검토 기록' for e in a['events'])
        assert len(processing_events(db,1)['events'])==1


def test_runtime_prepares_off_request_path_and_deduplicates(tmp_path):
    import threading,time
    from unittest.mock import patch
    from observatory_runtime import ObservatoryRuntime
    gate=threading.Event()
    def build(*args):
        gate.wait(2);return {'days':DAYS,'version':'v'}
    with patch('observatory_runtime.read_observatory',side_effect=build) as mock:
        service=ObservatoryRuntime(tmp_path/'db')
        try:
            started=time.monotonic()
            for _ in range(10):assert service.request()['status']=='preparing'
            assert time.monotonic()-started<.5
            gate.set()
            deadline=time.monotonic()+3
            while service.pending and time.monotonic()<deadline:time.sleep(.01)
            assert service.request()['days']==DAYS and mock.call_count==1
        finally:gate.set();service.close()


def test_runtime_serves_saved_window_before_background_rebuild(tmp_path):
    import json,threading
    from unittest.mock import patch
    from observatory_runtime import ObservatoryRuntime
    path=tmp_path/'db';directory=tmp_path/'db.observatory';directory.mkdir()
    days=[(date(2026,6,19)+timedelta(days=i)).isoformat() for i in range(90)]
    (directory/'90-v2.json').write_text(json.dumps({'window':90,'data':{'days':days,'version':'saved'}}))
    gate=threading.Event()
    with patch('observatory_runtime.read_observatory',side_effect=lambda *args:(gate.wait(2) or {'days':days})):
        service=ObservatoryRuntime(path)
        try:
            result=service.request(90)
            assert result['version']=='saved' and result['refreshing']
        finally:gate.set();service.close()


def test_selected_period_changes_topics_keywords_and_equal_period_comparison(tmp_path):
    import sqlite3
    from unittest.mock import patch
    from observatory import read_observatory
    end=date(2026,9,16)
    items=[];morph={}
    for index,(age,topic) in enumerate([(1,'recent'),(2,'recent'),(20,'older'),(21,'older'),(45,'historic'),(46,'historic'),(100,'historic')]):
        item=dict(chat_id=1,message_id=index,item_index=0,day=(end-timedelta(days=age)).isoformat(),title=topic,text=topic,source_url=f'https://example.org/{index}')
        items.append(item);morph[keyword_record_id(item)]=[dict(id=topic,label=topic,kind='noun_phrase')]
    lenses=[dict(id=t,label=t,current=0,previous=0,dynamic=True) for t in ('recent','older','historic','empty')]
    with sqlite3.connect(tmp_path/'period.db') as db, \
         patch('projection_cache.revision_token',return_value=('source',1)), \
         patch('dynamic_registry.list_registry',return_value={'version':1,'items':[]}), \
         patch('strategy_views.dataset',return_value={'items':items,'morph':morph}), \
         patch('observatory.candidate_context',return_value={'end':end.isoformat(),'lenses':lenses,'monitoring':{'topics':[]}}), \
         patch('strategy.select_strategy_items',side_effect=lambda db,p,rows,*a,**kw:[i for i in rows if i['title']==p['lens'][0]]):
        views=[read_observatory(db,w) for w in (14,30,90)]
    assert [{n['id'] for n in v['nodes'] if n['kind']=='topic'} for v in views]==[{'recent'},{'recent','older'},{'recent','older','historic'}]
    assert [len([n for n in v['nodes'] if n['kind']=='keyword']) for v in views]==[1,2,3]
    historic=next(n for n in views[2]['nodes'] if n['id']=='historic')
    assert (historic['current'],historic['previous'])==(2,1)
    assert [v['comparison_days'] for v in views]==[14,30,90]
    assert all(n['current']==n['count'] for v in views for n in v['nodes'])
    assert views[2]['comparison']['previous_observed_days']==1


def test_periods_reuse_membership_checks_and_registry_change_invalidates(tmp_path):
    import sqlite3
    from unittest.mock import patch
    from observatory import read_observatory
    items,morph=fixture();entry=dict(id='dynamic:test',label='a',current=2,previous=0,dynamic=True)
    registry={'version':1,'items':[dict(id=entry['id'],kind='topic',origin='automatic',metadata={'keyword_id':'a'},excluded=False)]}
    calls=[]
    def select(db,params,rows,*args,**kwargs):
        if params['lens']==['dynamic:test']:
            calls.append(list(rows))
            assert all(any(t['id']=='a' for t in morph[keyword_record_id(i)]) for i in rows)
            return rows
        return []
    with sqlite3.connect(tmp_path/'reuse.db') as db, \
         patch('projection_cache.revision_token',return_value=('source',1)), \
         patch('dynamic_registry.list_registry',side_effect=lambda db:registry), \
         patch('strategy_views.dataset',return_value={'items':items,'morph':morph}), \
         patch('observatory.candidate_context',return_value={'end':DAYS[-1],'lenses':[entry],'monitoring':{'topics':[]}}), \
         patch('strategy.select_strategy_items',side_effect=select):
        for w in (14,30,90):read_observatory(db,w)
        assert len(calls)==1
        registry['version']=2
        registry['items'][0]['terms']=['changed']
        read_observatory(db,14)
        assert len(calls)==2
def test_candidate_context_does_not_build_seven_day_overview():
    import sqlite3
    from unittest.mock import patch
    from observatory import candidate_context
    with sqlite3.connect(':memory:') as db, \
         patch('dynamic_strategy.dynamic_projection', return_value={'topics': [{'id':'dynamic:one','dynamic':True}]}), \
         patch('dynamic_registry.list_registry', return_value={'items':[{'id':'china','excluded':True}]}), \
         patch('strategy_views.read_view', side_effect=AssertionError('unrelated overview')):
        context = candidate_context(db, {'items':[{'day':'2026-09-18'},{'day':'invalid'}], 'morph':{}})
    assert context['end'] == '2026-09-18'
    assert 'china' not in {t['id'] for t in context['lenses']}
    assert 'dynamic:one' in {t['id'] for t in context['lenses']}
