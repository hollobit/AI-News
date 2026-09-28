import sqlite3
import unittest
from dynamic_cooccurrence import build_cooccurrence_candidates,match_cooccurrence_item
from dynamic_registry import list_registry,set_excluded,sync_automatic
from keyword_index import keyword_record_id

class CooccurrenceTests(unittest.TestCase):
    def setUp(self):self.db=sqlite3.connect(':memory:')
    def tearDown(self):self.db.close()
    def fixtures(self,split=False):
        items=[];morph={}
        for number in range(2):
            item={'title':'','text':'AI 소버린 컴퓨팅 GPU 공급망','day':'2026-09-15',
                  'source_url':f'https://example.com/{number}','id':str(number),'message_id':number+1}
            if split:
                item['text']='AI 소버린 컴퓨팅'
                item['source_context']={'status':'fetched','title':'','text':'AI GPU 공급망'}
            from dynamic_topics import _segments
            terms=[]
            for identity,label in [('a','소버린 컴퓨팅'),('b','GPU 공급망')]:
                for offset,text,_ in _segments(item):
                    if label in text:
                        start=offset+text.index(label)
                        terms.append({'id':identity,'label':label,'surface':label,'kind':'noun_phrase','start':start,'end':start+len(label)})
            items.append(item);morph[keyword_record_id(item)]=terms
        return items,morph

    def test_same_source_offsets_and_stats(self):
        items,morph=self.fixtures()
        values=build_cooccurrence_candidates(self.db,items,morph)
        self.assertEqual(len(values),1)
        value=values[0]
        self.assertEqual(value['documents'],2)
        self.assertEqual(value['current'],2)
        self.assertEqual(len(value['series']),14)
        self.assertEqual(value['match_mode'],'all')
        self.assertTrue(match_cooccurrence_item(items[0],value,morph[keyword_record_id(items[0])]))
        for evidence in value['evidence']:
            for quote in evidence['quotes']:
                self.assertEqual(items[0]['text'][quote['term_start']:quote['term_end']],quote['surface'])

    def test_cross_source_and_duplicate_url_do_not_qualify(self):
        items,morph=self.fixtures(split=True)
        self.assertEqual(build_cooccurrence_candidates(self.db,items,morph),[])
        self.assertFalse(match_cooccurrence_item(items[0],{'terms':['소버린 컴퓨팅','GPU 공급망']},morph[keyword_record_id(items[0])]))
        items,morph=self.fixtures()
        duplicate=dict(items[0])
        self.assertEqual(build_cooccurrence_candidates(None,[items[0],duplicate],morph),[])

    def test_namespace_tombstone_and_unchanged_revision(self):
        items,morph=self.fixtures()
        value=build_cooccurrence_candidates(self.db,items,morph)[0]
        before=list_registry(self.db)['version']
        build_cooccurrence_candidates(self.db,items,morph)
        self.assertEqual(list_registry(self.db)['version'],before)
        sync_automatic(self.db,[{'id':'dynamic:one','label':'AI 규제','terms':['AI 규제']}])
        records={r['id']:r for r in list_registry(self.db)['items']}
        self.assertEqual(records[value['id']]['status'],'active')
        build_cooccurrence_candidates(self.db,[],{})
        records={r['id']:r for r in list_registry(self.db)['items']}
        self.assertEqual(records['dynamic:one']['status'],'active')
        set_excluded(self.db,value['id'],True)
        self.assertEqual(build_cooccurrence_candidates(self.db,items,morph),[])
        set_excluded(self.db,value['id'],False)
        self.assertEqual(len(build_cooccurrence_candidates(self.db,items,morph)),1)

    def test_subphrase_pairs_are_not_new_strategic_candidates(self):
        items,morph=self.fixtures()
        for item in items:
            terms=morph[keyword_record_id(item)]
            original=terms[1]
            terms[0]=dict(original,id='short',label='GPU',surface='GPU',end=original['start']+3)
        self.assertEqual(build_cooccurrence_candidates(self.db,items,morph),[])

    def test_registered_candidate_survives_new_discovery_cap(self):
        items,morph=self.fixtures()
        old=build_cooccurrence_candidates(self.db,items,morph)[0]
        for number in range(3):
            labels=['기술'+letter for letter in '가나다라마바사아자차']
            item={'title':'','text':'AI '+' '.join(labels),'day':'2026-09-15','source_url':f'https://new.example/{number}','id':'new'+str(number),'message_id':100+number}
            terms=[]
            for index,label in enumerate(labels):
                start=1+item['text'].index(label)
                terms.append({'id':'new'+str(index),'label':label,'surface':label,'kind':'noun_phrase','start':start,'end':start+len(label)})
            items.append(item);morph[keyword_record_id(item)]=terms
        result=build_cooccurrence_candidates(self.db,items,morph)
        retained=next(entry for entry in result if entry['id']==old['id'])
        self.assertEqual(retained['current'],2)
        self.assertEqual(retained['registry_status'],'active')
        self.assertEqual(len(result),33)
        version=list_registry(self.db)['version']
        build_cooccurrence_candidates(self.db,items,morph)
        self.assertEqual(list_registry(self.db)['version'],version)

    def test_manual_cooccurrence_preserves_all_and_reobserves_changed_terms(self):
        from dynamic_registry import save_manual
        items,morph=self.fixtures()
        old=build_cooccurrence_candidates(self.db,items,morph)[0]
        manual=save_manual(self.db,{'id':old['id'],'label':'사용자 관찰','terms':old['terms'],'metadata':{}})
        self.assertEqual(manual['metadata']['match_mode'],'all')
        changed=save_manual(self.db,{'id':old['id'],'label':'사용자 관찰','terms':['소버린 컴퓨팅','미관측 기술'],'metadata':{}})
        self.assertEqual(changed['metadata']['keyword_ids'],[])
        result=next(entry for entry in build_cooccurrence_candidates(self.db,items,morph) if entry['id']==old['id'])
        self.assertEqual(result['match_mode'],'all')
        self.assertEqual(result['current'],0)
        self.assertEqual(result['status'],'dormant')
        self.assertEqual(result['evidence'],[])
        self.assertFalse(match_cooccurrence_item(items[0],result,morph[keyword_record_id(items[0])]))

    def test_corrupt_offsets_do_not_become_evidence(self):
        items,morph=self.fixtures()
        for terms in morph.values():terms[0]['start']+=1
        self.assertEqual(build_cooccurrence_candidates(self.db,items,morph),[])
