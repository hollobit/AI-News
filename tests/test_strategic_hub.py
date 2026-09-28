import tempfile
import unittest
from unittest.mock import patch

from keyword_index import keyword_record_id
from strategic_hub import StrategicHub,collection_scores
from strategic_assessments import default_profile


def fake_morphology(db,items):
    return {keyword_record_id(i):[{'id':'ai-safety','label':'AI 안전성','surface':'AI 안전성','kind':'technical_dictionary'}] for i in items}


class HubTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.morph=patch('morphology.keyword_records',side_effect=fake_morphology);self.morph.start();self.addCleanup(self.morph.stop)
        self.hub=StrategicHub(self.directory.name+'/hub.db',start_worker=False,enabled=False)
        self.addCleanup(self.hub.close)
        self.items=[{'id':str(n),'source_url':f'https://example.org/{n}','title':f'AI 안전성 소식 {n}','text':f'AI 안전성 원문 {n}',
                     'day':f'2026-09-{n+1:02d}','date_basis':'article'} for n in range(3)]

    def test_empty_database_views_and_sync_without_risk_tables(self):
        self.assertIsNone(self.hub.worker)
        for view in ('overview','documents','claims','events','topics','concepts','decisions','scenarios','profiles','research','operations','experiments','risk_history'):
            with self.subTest(view=view):
                result=self.hub.get(view)
                self.assertIn('items',result)
                self.assertEqual(0,result['total'])
        runtime=self.hub.sync(self.items)
        self.assertEqual('ready',runtime['status'])
        self.assertEqual(3,self.hub.get('overview')['metrics']['documents'])
        self.assertEqual(0,runtime['changes']['changed_risk_observations'])
        self.assertEqual(0,self.hub.get('risk_history')['total'])
        self.assertEqual(3,self.hub.get('coverage')['current'])
        self.assertEqual(3,len(self.hub.get('momentum')['windows']))

    def test_pagination_detail_and_no_fake_current_risk(self):
        self.hub.sync(self.items)
        listing=self.hub.get('documents',{'page_size':['2']})
        self.assertEqual(3,listing['total']);self.assertEqual(2,listing['total_pages'])
        self.assertNotIn('text',listing['items'][0])
        self.assertIsNone(listing['items'][0]['scores']['risk'])
        detail=self.hub.get('document',{'id':listing['items'][0]['id']})
        self.assertIn('text',detail['item']);self.assertTrue(detail['history'])
        for plural,singular in [('events','event'),('topics','topic'),('concepts','concept')]:
            item=self.hub.get(plural)['items'][0]
            self.assertEqual(item['id'],self.hub.get(singular,{'id':item['id']})['item']['id'])
        self.assertIsNone(self.hub.get('document',{'id':'missing'})['item'])
        event=self.hub.get('events')['items'][0]
        members=self.hub.get('documents',{'event_id':event['id']})
        self.assertEqual(set(event['document_ids']),{d['id'] for d in members['items']})
        self.assertEqual(0,self.hub.get('documents',{'event_id':'missing'})['total'])

    def test_decision_scenario_and_evidence_change_review(self):
        self.hub.sync(self.items)
        doc=self.hub.get('documents')['items'][0];topic=self.hub.get('topics')['items'][0]
        decision=self.hub.mutate('decisions',{'action':'create','title':'도입 선택','question':'어떤 방식을 선택할까',
            'topic_id':topic['id'],'evidence_ids':[doc['id']],'options':[{'id':'gradual','label':'단계 도입'}],
            'selected_option_id':'gradual','rationale':'원문 조건 확인'})['item']
        updated=self.hub.mutate('decisions',{'action':'update','id':decision['id'],'owner':'검토자'})['item']
        self.assertEqual(2,updated['version']);self.assertEqual('gradual',updated['selected_option_id'])
        scenario=self.hub.mutate('scenarios',{'action':'create','title':'조건부 시나리오','decision_id':decision['id'],
            'evidence_ids':[doc['id']],'assumptions':[{'name':'AI 통제','value':'시행할 경우','status':'hypothetical'}]})['item']
        self.assertEqual('hypothetical',scenario['assumptions'][0]['status'])
        self.assertEqual(decision['id'],self.hub.get('scenarios',{'id':scenario['id']})['item']['decision_id'])
        for item in self.items:item['text']+=' 정정'
        self.hub.sync(self.items)
        reviewed=self.hub.mutate('decisions',{'action':'review','id':decision['id']})['item']
        self.assertEqual('needs_review',reviewed['review_state'])
        self.assertEqual('gradual',reviewed['selected_option_id'])
        self.assertGreater(self.hub.get('overview')['metrics']['review_needed'],0)
        with self.assertRaises(ValueError):self.hub.mutate('scenarios',{'action':'compare','ids':[scenario['id'],scenario['id']]})

    def test_profiles_source_attribution_and_experiment_candidates(self):
        self.hub.sync(self.items)
        profile=self.hub.mutate('profiles',{'action':'save','name':'안보 검토','weights':{'importance':4,'risk':3,'evidence':2,'opportunity':1}})['item']
        scored=self.hub.get('documents',{'profile_id':profile['id']})['items'][0]
        self.assertEqual(profile['id'],scored['priority']['profile_id'])
        attribution=self.hub.mutate('events',{'action':'source','document_id':scored['id'],
            'original_source_url':'https://agency.org/statement','independence':'derived','reason':'같은 발표를 인용'})
        self.assertTrue(attribution['refresh_pending']);self.hub.sync(self.items)
        doc=self.hub.get('document',{'id':scored['id']})['item']
        self.assertEqual('derived',doc['independent_confirmation'])
        self.assertEqual('explicit_user_review',doc['attribution_basis'])
        candidate=self.hub.mutate('experiments',{'action':'candidate','title':'날짜 확인','rules':['게시일과 원기사 날짜를 분리한다.']})['item']
        found=self.hub.get('experiments')
        self.assertIn(candidate['id'],[c['id'] for c in found['candidates']])
        self.assertEqual(0,found['total'])
        with self.assertRaises(ValueError):self.hub.mutate('query',{'question':'AI 안전성 현황'})

    def test_topic_average_does_not_assign_strongest_document_to_every_member(self):
        rows=[{'scores':{'importance':importance,'risk':risk,'evidence':evidence,'opportunity':None},
               'score_details':{'risk':{'range':[risk,risk] if risk is not None else [0,100]}}}
              for importance,risk,evidence in [(100,80,80),(0,None,0)]]
        scores,priority=collection_scores(rows,default_profile())
        self.assertEqual(50,scores['importance']);self.assertEqual(40,scores['evidence'])
        self.assertIsNone(scores['risk']);self.assertIsNone(priority['score'])
        self.assertGreater(priority['range'][1],priority['range'][0])
