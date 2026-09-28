import copy
import json
import sqlite3
import tempfile
import unittest
from strategic_jobs import StrategicJobs,validate_report


def snapshot():
    return {'kind':'scenario_compare','subjects':[{'id':'a'},{'id':'b'}],
            'evidence':[{'id':'d','version':1,'source_hash':'original','status':'current','text':'원문'}]}


def report():
    return {'summary':'조건에 따른 비교','comparisons':[{'subject_id':s,'title':s,'assessment':'조건부 해석','assumptions':['가정'], 'evidence_ids':['d']} for s in ('a','b')],'limitations':['현재 자료 범위']}


class JobTests(unittest.TestCase):
    def test_all_subjects_and_only_provided_evidence(self):
        value=report();value['comparisons'].pop()
        with self.assertRaises(ValueError):validate_report(value,snapshot())
        value=report();value['comparisons'][0]['evidence_ids']=['invented']
        with self.assertRaises(ValueError):validate_report(value,snapshot())

    def run_job(self,audit):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        current=copy.deepcopy(snapshot()['evidence']);self.current=current
        calls=[]
        def analyze(prompt,schema,role):
            calls.append(role)
            return copy.deepcopy(audit if role=='strategic_comparison_verification' else report())
        self.service=StrategicJobs(self.directory.name+'/jobs.db',analyzer=analyze,enabled=True,current_evidence=lambda ids:current)
        self.addCleanup(self.service.close)
        result=self.service.start(snapshot());identity=result['id']
        self.service.futures[identity].result(timeout=5)
        return identity,calls

    def test_independent_audit_required_and_source_change_is_not_verified(self):
        identity,calls=self.run_job({'accepted':True,'issues':[],'checked_evidence_ids':['d'],'assumptions_separated':True,'limitations':['범위 제한']})
        self.assertIn('strategic_comparison_verification',calls)
        self.assertTrue(self.service.get(identity)['result']['verified'])
        self.current[0]['version']=2
        stale=self.service.get(identity)
        self.assertEqual('needs_review',stale['status'])
        self.assertFalse(stale['result']['verified'])

    def test_refused_audit_never_accepted(self):
        identity,_=self.run_job({'accepted':False,'issues':['가정을 사실로 혼동'],'checked_evidence_ids':['d'],'assumptions_separated':False,'limitations':['검토 필요']})
        value=self.service.get(identity)
        self.assertEqual('needs_review',value['status']);self.assertFalse(value['result']['verified'])

    def test_saved_report_tampering_is_not_verified(self):
        identity,_=self.run_job({'accepted':True,'issues':[],'checked_evidence_ids':['d'],'assumptions_separated':True,'limitations':['범위 제한']})
        with sqlite3.connect(self.service.path) as db:
            raw=json.loads(db.execute('SELECT result_json FROM intel_analysis_jobs WHERE id=?',(identity,)).fetchone()[0])
            raw['report']['summary']='unreviewed replacement'
            db.execute('UPDATE intel_analysis_jobs SET result_json=? WHERE id=?',(json.dumps(raw),identity))
        self.assertFalse(self.service.get(identity)['result']['verified'])
