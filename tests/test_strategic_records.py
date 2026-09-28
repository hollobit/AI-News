import sqlite3
import unittest
from strategic_records import init_records,decision_value,scenario_value,save_record,refresh_record,record_history,validate_refs


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');init_records(self.db)
        self.docs={'d':{'id':'d','version':1,'status':'current','title':'학습 제한을 검토한다','text':''}}
    def tearDown(self):self.db.close()

    def test_changed_evidence_requests_review_without_changing_decision(self):
        value=decision_value({'title':'도입 검토','question':'어떻게 도입할까','evidence_ids':['d'],
            'support_conditions':['학습 제한'],'options':[{'id':'a','label':'점진 도입'}],
            'selected_option_id':'a','rationale':'조건 확인 후 진행'},self.docs.get)
        old=save_record(self.db,'decision',value,'initial')
        first=refresh_record(self.db,old,self.docs.get)
        self.assertEqual('mentioned',first['condition_observations'][0]['state'])
        self.docs['d']['version']=2
        changed=refresh_record(self.db,first,self.docs.get)
        self.assertEqual('needs_review',changed['review_state'])
        self.assertEqual('a',changed['selected_option_id'])
        self.assertGreater(len(record_history(self.db,old['id'])),1)
        again=refresh_record(self.db,changed,self.docs.get)
        self.assertEqual(changed['version'],again['version'])

    def test_missing_and_removed_new_evidence_rejected(self):
        with self.assertRaises(ValueError):validate_refs(['missing'],self.docs.get)
        self.docs['d']['status']='removed'
        with self.assertRaises(ValueError):validate_refs(['d'],self.docs.get)

    def test_hypothesis_separate_and_missing_assumption_reference_rejected(self):
        value=scenario_value({'title':'정책 시나리오','assumptions':[{'name':'규제','value':'시행 가정','status':'hypothetical','evidence_ids':['d']}]},self.docs.get)
        self.assertEqual('hypothetical',value['assumptions'][0]['status'])
        self.assertEqual(['d'],value['evidence_ids'])
        with self.assertRaises(ValueError):scenario_value({'title':'x','assumptions':[{'name':'x','value':'x','evidence_ids':['missing']}]},self.docs.get)
