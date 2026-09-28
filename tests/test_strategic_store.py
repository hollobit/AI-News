import json
import sqlite3
import unittest
from unittest.mock import patch
from strategic_store import sync_store,query_store,mutate_store,get_document,init_store
from keyword_index import keyword_record_id


def morphology(db,items):
    return {keyword_record_id(item):[{'id':'quantum','label':'양자컴퓨팅','surface':'양자컴퓨팅','kind':'technical_dictionary'}] for item in items if '양자컴퓨팅' in item['text']}


def item(url='https://example.org/a',text='양자컴퓨팅 발표',day='2026-09-15',title='양자컴퓨팅 발표'):
    return {'id':url,'source_url':url,'title':title,'text':text,'day':day,'date_basis':'article','channel':'a'}


@patch('morphology.keyword_records',side_effect=morphology)
class StoreTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:')
    def tearDown(self):self.db.close()

    def test_unchanged_sync_versions_and_duplicate_url_counts(self,_):
        a=item();first=sync_store(self.db,[a]);second=sync_store(self.db,[a,a])
        self.assertEqual(first['version'],second['version'])
        self.assertEqual(1,query_store(self.db,'events')['total'])
        trend=query_store(self.db,'momentum',{'date':'2026-09-15'})
        self.assertEqual(1,trend['windows'][0]['current']['urls'])
        self.assertEqual(0,trend['windows'][0]['current']['original_sources'])
        self.assertTrue(trend['windows'][0]['low_sample'])
        self.assertIsNone(trend['windows'][0]['growth']['urls'])

    def test_only_dependent_event_changes_and_history_kept(self,_):
        a=item();b=item('https://example.org/b','unrelated','2026-09-14','different')
        sync_store(self.db,[a,b]);before={e['id']:e for e in query_store(self.db,'events')['items']}
        a['text']+=' 수정';sync_store(self.db,[a,b]);after={e['id']:e for e in query_store(self.db,'events')['items']}
        versions=[after[k]['version']-before[k]['version'] for k in before]
        self.assertEqual([0,1],sorted(versions))
        doc=query_store(self.db,'documents',{'q':'양자'})['items'][0]
        self.assertEqual(2,len(query_store(self.db,'history',{'id':doc['id']})['items']))
        self.assertEqual(doc['id'],get_document(self.db,doc['id'])['id'])

    def test_manual_merge_split_exclusion_persist(self,_):
        data=[item(),item('https://other.org/b',day='2026-09-14',title='other')]
        sync_store(self.db,data);events=query_store(self.db,'events')['items']
        result=mutate_store(self.db,'event_merge',{'ids':[e['id'] for e in events],'reason':'same announced event'})
        sync_store(self.db,data)
        current=[e for e in query_store(self.db,'events')['items'] if e['status']!='dormant']
        self.assertEqual(1,len(current));self.assertIsNone(current[0]['independent_confirmation_count'])
        mutate_store(self.db,'event_split',{'id':result['target_event_id'],'document_ids':[current[0]['document_ids'][0]],'reason':'different event'})
        sync_store(self.db,data)
        self.assertEqual(2,len([e for e in query_store(self.db,'events')['items'] if e['status']!='dormant']))
        cid=query_store(self.db,'concepts')['items'][0]['id']
        mutate_store(self.db,'exclude',{'id':cid,'excluded':True,'reason':'not strategic'})
        sync_store(self.db,data);self.assertEqual(0,query_store(self.db,'concepts')['total'])

    def test_alias_history_and_source_removal(self,_):
        sync_store(self.db,[item()]);cid=query_store(self.db,'concepts')['items'][0]['id']
        mutate_store(self.db,'concept_alias',{'concept_id':cid,'alias':'量子计算','language':'zh','reason':'manual translation review'})
        sync_store(self.db,[item()]);concept=query_store(self.db,'concept',{'id':cid})
        self.assertEqual('量子计算',concept['aliases'][0]['surface'])
        sync_store(self.db,[])
        self.assertEqual('dormant',query_store(self.db,'concept',{'id':cid})['status'])
        self.assertEqual('removed',query_store(self.db,'documents')['items'][0]['status'])

    def test_source_attribution_persists_and_tracks_independence(self,_):
        data=[item(),item('https://other.org/b',title='second report')]
        sync_store(self.db,data)
        for doc in query_store(self.db,'documents')['items']:
            mutate_store(self.db,'source_attribution',{'document_id':doc['id'],'original_source_url':'https://agency.org/announcement','independence':'derived','reason':'Both cite same announcement'})
        sync_store(self.db,data)
        events=[e for e in query_store(self.db,'events')['items'] if e['status']!='dormant']
        self.assertEqual(1,len(events))
        self.assertIsNone(events[0]['independent_confirmation_count'])
        self.assertEqual(1,query_store(self.db,'momentum',{'date':'2026-09-15'})['windows'][0]['current']['original_sources'])

    def test_reviewed_claim_current_source_gate_and_targeted_invalidation(self,_):
        import hashlib
        data=[item(),item('https://other.org/b',text='other',title='other')]
        self.db.execute('CREATE TABLE strategic_workflow_runs(id,status,error)')
        self.db.execute('CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json)')
        for n,doc in enumerate(data):
            evidence=[{'id':'e','url':doc['source_url'],'title':doc['title'],'text':doc['title']+'\n\n'+doc['text'],'origin':'telegram_excerpt'}]
            report={'claims':[{'title':'claim'+str(n),'detail':'reviewed interpretation','uncertainty':'not established','evidence_ids':['e']}]}
            digest=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            payload={'verified':True,'report':report,'evidence':evidence,'verification':{'accepted':True,'issues':[],'checked_evidence_ids':['e'],'report_hash':digest(report),'evidence_hash':digest(evidence)}}
            self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?)',(str(n),'complete',None))
            self.db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(str(n),'final',json.dumps(payload)))
        self.db.commit()
        sync_store(self.db,data)
        self.assertEqual({'current_reviewed'},{c['status'] for c in query_store(self.db,'claims')['items']})
        data[0]['text']='corrected source'
        changed=sync_store(self.db,data)
        self.assertEqual(1,changed['invalidated_claims'])
        claims=query_store(self.db,'claims')['items']
        self.assertEqual(['current_reviewed','needs_review'],sorted(c['status'] for c in claims))
        again=sync_store(self.db,data)
        self.assertEqual(changed['version'],again['version'])

    def test_multilingual_alias_merge_split_and_original_expressions(self,_):
        def terms(db,items):
            return {keyword_record_id(i):[{'id':i['text'],'label':i['text'],'surface':i['text'],'kind':'technical_dictionary','start':0,'end':len(i['text'])}] for i in items}
        data=[item(text='양자컴퓨팅'),item('https://example.org/en',text='quantum computing'),item('https://example.org/zh',text='量子计算')]
        with patch('morphology.keyword_records',side_effect=terms):
            sync_store(self.db,data)
            cid=next(c['id'] for c in query_store(self.db,'concepts')['items'] if c['label']=='양자컴퓨팅')
            for name,lang in [('quantum computing','en'),('量子计算','zh')]:
                mutate_store(self.db,'concept_alias',{'concept_id':cid,'alias':name,'language':lang,'reason':'reviewed translation'})
            sync_store(self.db,data)
            combined=query_store(self.db,'concept',{'id':cid})
            self.assertEqual(3,len(combined['document_ids']))
            self.assertEqual({'양자컴퓨팅','quantum computing','量子计算'},{e['surface'] for e in combined['expressions']})
            split=mutate_store(self.db,'concept_split',{'concept_id':cid,'aliases':['量子计算'],'label':'중국어 별도 검토','reason':'separate scope'})
            sync_store(self.db,data)
            self.assertEqual(2,len(query_store(self.db,'concept',{'id':cid})['document_ids']))
            self.assertEqual('중국어 별도 검토',query_store(self.db,'concept',{'id':split['concept_id']})['label'])

    def test_collection_scope_changes_are_not_growth_confirmation(self,_):
        data=[item()]
        sync_store(self.db,data)
        newer=item('https://example.org/new');newer['channel']='new-channel'
        sync_store(self.db,data+[newer])
        coverage=query_store(self.db,'coverage')
        self.assertEqual(2,len(coverage['collection_scope_history']))
        self.assertTrue(query_store(self.db,'momentum',{'date':'2026-09-15'})['windows'][0]['coverage_changed'])

    def test_uncited_fetched_change_invalidates_claim_and_url_free_text_matches(self,_):
        import hashlib
        data=[item(),item('',text='URL 없는 원문',title='텍스트 소식')]
        data[0]['source_context']={'status':'fetched','title':'Publisher title','text':'Original publisher excerpt'}
        self.db.execute('CREATE TABLE strategic_workflow_runs(id,status,error)')
        self.db.execute('CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json)')
        for index,doc in enumerate(data):
            evidence=[{'id':'tg','url':doc['source_url'],'title':doc['title'],'text':doc['title']+'\n\n'+doc['text'],'origin':'telegram_excerpt'}]
            if index==0:evidence.append({'id':'fetched','url':doc['source_url'],'title':'Publisher title','text':'Original publisher excerpt','origin':'fetched_url_excerpt'})
            report={'claims':[{'title':'claim'+str(index),'detail':'reviewed','uncertainty':'limited','evidence_ids':['tg']}]}
            digest=lambda value:hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            payload={'verified':True,'report':report,'evidence':evidence,'verification':{'accepted':True,'issues':[],'checked_evidence_ids':['tg'],'report_hash':digest(report),'evidence_hash':digest(evidence)}}
            self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?)',(str(index),'complete',None))
            self.db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(str(index),'final',json.dumps(payload)))
        self.db.commit();sync_store(self.db,data)
        self.assertEqual({'current_reviewed'},{c['status'] for c in query_store(self.db,'claims')['items']})
        data[0]['source_context']['text']='Changed publisher excerpt'
        sync_store(self.db,data)
        claims={c['workflow_run_id']:c for c in query_store(self.db,'claims')['items']}
        self.assertEqual('needs_review',claims['0']['status'])
        self.assertEqual('current_reviewed',claims['1']['status'])
        self.assertTrue(claims['1']['document_ids'])

    def test_telegram_date_never_becomes_article_publication(self,_):
        source=item(day='2026-09-15');source['date_basis']='telegram';source['published_at']='2026-09-15T12:00:00Z'
        sync_store(self.db,[source]);doc=query_store(self.db,'documents')['items'][0]
        self.assertEqual('',doc['published_day'])
        self.assertEqual('2026-09-15T12:00:00Z',doc['telegram_published_at'])
        self.assertEqual('',doc['collected_at'])
        self.assertEqual('unknown',doc['collection_date_basis'])
        momentum=query_store(self.db,'momentum',{'date':'2026-09-15'})
        self.assertEqual(1,momentum['undated_documents'])
        self.assertEqual(0,momentum['windows'][0]['current']['urls'])

    def test_shared_telegram_message_does_not_merge_distinct_url_free_articles(self,_):
        from improvement_selection import content_identity
        first=item('',text='양자컴퓨팅 첫 번째 기사',title='첫 기사')
        second=item('',text='다른 두 번째 기사',title='두 번째 기사')
        for entry in (first,second):entry['url']='https://t.me/channel/1234'
        result=sync_store(self.db,[first,second])
        self.assertEqual(2,result['documents'])
        docs=query_store(self.db,'documents')['items']
        self.assertEqual(2,len(docs))
        self.assertEqual({content_identity(first),content_identity(second)},{d['corpus_identity'] for d in docs})
        self.assertEqual({''},{d['source_url'] for d in docs})
        self.assertEqual({'https://t.me/channel/1234'},{d['archive_url'] for d in docs})
        repeated=dict(first,title='  첫 기사  ',text='양자컴퓨팅   첫 번째 기사')
        self.assertEqual(2,sync_store(self.db,[first,second,repeated])['documents'])

    def test_reviewed_event_observations_keep_disagreement_and_source_gate(self,_):
        import copy,hashlib
        doc=item();self.db.execute('CREATE TABLE strategic_workflow_runs(id,status,error)')
        self.db.execute('CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json)')
        evidence=[{'id':'e','url':doc['source_url'],'title':doc['title'],'text':doc['title']+'\n\n'+doc['text'],'origin':'telegram_excerpt'}]
        event={'actor':'기관 A','action':'제한을 제안했다','target':'AI 학습','outcome':'','occurred_at':'','actor_countries':[],'affected_countries':[],'evidence_ids':['e'],'uncertainty':'시행 여부 미확인'}
        def save(run,observation):
            report={'claims':[{'title':'조건 검토','detail':'제안의 조건을 검토한다','uncertainty':'한정된 근거','evidence_ids':['e']}],'event_observations':[observation]}
            digest=lambda x:hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            payload={'verified':True,'report':report,'evidence':evidence,'event_observations':[observation],
                'verification':{'accepted':True,'issues':[],'checked_evidence_ids':['e'],'checked_event_indices':[0],
                    'event_hash':digest([observation]),'report_hash':digest(report),'evidence_hash':digest(evidence)}}
            self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?)',(run,'complete',None))
            self.db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(run,'final',json.dumps(payload)))
            self.db.commit()
        save('first',event);sync_store(self.db,[doc])
        observed=query_store(self.db,'events')['items'][0]
        self.assertEqual('기관 A',observed['actor']);self.assertIsNone(observed['occurred_at'])
        self.assertEqual('시행 여부 미확인',observed['semantic_observations'][0]['uncertainty'])
        second=copy.deepcopy(event);second['action']='제안을 철회했다'
        save('second',second);sync_store(self.db,[doc])
        observed=query_store(self.db,'events')['items'][0]
        self.assertEqual(2,len(observed['semantic_observations']));self.assertIsNone(observed['actor'])
        doc['text']='변경된 원문';sync_store(self.db,[doc])
        observed=query_store(self.db,'events')['items'][0]
        self.assertEqual([],observed['semantic_observations']);self.assertIsNone(observed['action'])
        self.assertEqual('needs_review',observed['status'])

    def test_growth_acceleration_needs_three_comparable_periods(self,_):
        data=[]
        for day,count in [('2026-09-10',5),('2026-09-17',10),('2026-09-24',30)]:
            for n in range(count):data.append(item('https://example.org/'+day+'/'+str(n),day=day))
        sync_store(self.db,data)
        week=query_store(self.db,'momentum',{'date':'2026-09-30'})['windows'][0]
        self.assertEqual(2,week['growth']['urls']);self.assertEqual(1,week['previous_growth']['urls'])
        self.assertEqual(1,week['acceleration']['urls'])
        data[0]['channel']='extra-channel';sync_store(self.db,data)
        week=query_store(self.db,'momentum',{'date':'2026-09-30'})['windows'][0]
        self.assertIsNone(week['acceleration']['urls'])
        self.assertIn('channel_coverage_changed',week['cautions'])

    def test_webpage_menus_do_not_promote_topics_but_real_facebook_news_does(self,_):
        labels=['페이스북','트위터','카카오톡','인용안내','전문가포럼','지역메뉴']
        def terms(db,rows):
            return {keyword_record_id(row):[{'id':label,'label':label,'surface':label,'kind':'proper_noun'} for label in labels] for row in rows}
        data=[item('https://example.org/'+str(n),text='양자컴퓨팅 소식',title='양자 소식',day='2026-09-0'+str(n+1)) for n in range(3)]
        for row in data:row['source_context']={'status':'fetched','title':'양자컴퓨팅 연구 발표','text':'페이스북\n트위터\n카카오톡\n인용안내\n전문가포럼\n지역메뉴\n연구 지원 전문가포럼 지역메뉴 인용안내 안내사항'}
        with patch('morphology.keyword_records',side_effect=terms):
            sync_store(self.db,data)
            self.assertEqual(6,query_store(self.db,'concepts')['total'])
            self.assertEqual(0,query_store(self.db,'topics')['total'])
            for row in data:row['source_context']['text']+='\n페이스북은 인공지능 안전성 연구 결과와 신규 모델을 발표했다.'
            sync_store(self.db,data)
            observed=[t for t in query_store(self.db,'topics')['items'] if t['status']!='dormant']
            self.assertEqual(['페이스북'],[t['label'] for t in observed])
            self.assertEqual(3,len(observed[0]['document_ids']))
            self.assertTrue(all(c['field']=='source_text' for c in observed[0]['supporting_contexts']))
            # A genuine Telegram headline remains valid without a source prose verb.
            for row in data:row['title']='트위터 AI 연구 확대';row['source_context']['text']='인용안내\n페이스북'
            sync_store(self.db,data)
            observed=[t for t in query_store(self.db,'topics')['items'] if t['status']!='dormant']
            self.assertEqual(['트위터'],[t['label'] for t in observed])


    def test_dormant_topics_hidden_by_default_with_history_access(self,_):
        sync_store(self.db,[item()]);identity=query_store(self.db,'topics')['items'][0]['id']
        sync_store(self.db,[])
        self.assertEqual(0,query_store(self.db,'topics')['total'])
        self.assertEqual(1,query_store(self.db,'topics',{'include_dormant':'true'})['total'])
        self.assertEqual(1,query_store(self.db,'topics',{'status':'dormant'})['total'])
        self.assertEqual('dormant',query_store(self.db,'topic',{'id':identity})['status'])

    def test_copyright_notice_and_version_like_nonmodels_not_topics(self,_):
        terms=[('전문가포럼','proper_noun'),('under-16s','model_identifier'),('CA-17','model_identifier'),('mid-2025','model_identifier'),('GPT4o','model_identifier'),('K2.5','model_identifier'),('V3.1','model_identifier')]
        def extracted(db,rows):
            return {keyword_record_id(r):[{'id':label,'label':label,'surface':label,'kind':kind} for label,kind in terms] for r in rows}
        data=[item('https://example.org/'+str(n),title='under-16s 정책 CA-17 지역구 mid-2025 일정',text='GPT4o 모델과 Kimi K2.5, DeepSeek V3.1 모델이 발표됐다.',day=f'2026-09-0{n+1}') for n in range(3)]
        for row in data:row['source_context']={'status':'fetched','title':'정책 소식','text':'자료를 인용, 보도하시는 경우, 출처를 반드시 “CSF(중국전문가포럼)”로 명시해 주시기 바랍니다.'}
        with patch('morphology.keyword_records',side_effect=extracted):
            sync_store(self.db,data)
            self.assertEqual(7,query_store(self.db,'concepts')['total'])
            self.assertEqual({'GPT4o','K2.5','V3.1'},{t['label'] for t in query_store(self.db,'topics')['items']})
