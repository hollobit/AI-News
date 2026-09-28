import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from evidence_corrections import correction_token, project_catalog
from graph_rag import _workflow_graph, load_integrated_graph


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = self.directory.name+'/test.sqlite'
        self.db = sqlite3.connect(self.path)
        self.db.execute('CREATE TABLE risk_review_resolutions(risk_id,status,reason,corrected_source_url,replacement_workflow_run_id,created_at)')
        self.db.execute('CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json)')
        self.risk = {'title':'wrong mapping','evidence_ids':['bad']}
        self.payload = {'verified':True,'risk_report':{'risks':[self.risk]},
            'report':{'claims':[{'title':title,'detail':title+' detail','uncertainty':'uncertain','evidence_ids':refs}
                               for title,refs in [('bad',['bad']),('mixed',['bad','good']),('good',['good'])]]},
            'evidence':[{'id':ref,'origin':'telegram_excerpt','text':ref,'url':'https://example.org/'+ref} for ref in ['bad','good']]}
        self.payload['verification']={'accepted':True,'issues':[],'checked_evidence_ids':['bad','good'],
            'report_hash':digest(self.payload['report']),'evidence_hash':digest(self.payload['evidence'])}
        self.db.execute("INSERT INTO strategic_workflow_artifacts VALUES ('old','final',?)",(json.dumps(self.payload),))
        self.db.commit()

    def tearDown(self):
        self.db.close();self.directory.cleanup()

    def withdraw(self):
        self.db.execute('INSERT INTO risk_review_resolutions VALUES (?,?,?,?,?,?)',
            ('risk:'+digest(['old',self.risk])[:24],'withdrawn_source_mismatch','wrong source','https://example.org/new','new','now'))
        self.db.commit()

    @patch('risk_analysis.validated_risk_content', return_value={'accepted':True})
    def test_claim_whole_exclusion_and_new_run_preserved(self, _):
        self.withdraw();token=correction_token(self.db)
        graph=_workflow_graph(self.payload,'old',token)
        self.assertEqual(['good'],[n['name'] for n in graph['nodes'] if n['type']=='StrategicClaim'])
        self.assertEqual(4,len(_workflow_graph(self.payload,'new',token)['edges']))
        self.assertEqual(3,len(self.payload['report']['claims']))

    def test_memory_projection_and_readonly_token(self):
        item={'id':'x','run_ids':['old'],'evidence':[{'id':'global','source_record_id':'bad','workflow_run_ids':['old']}],'evidence_ids':['global']}
        self.withdraw()
        with sqlite3.connect('file:'+self.path+'?mode=ro',uri=True) as reader:
            projected=project_catalog(reader,[item])[0]
            self.assertFalse(projected['retrieval_eligible'])
            self.assertEqual('new',projected['corrections'][0]['replacement_workflow_run_id'])
            self.assertEqual([],projected['evidence'])
        self.assertEqual(1,len(item['evidence']))

    @patch('risk_analysis.validated_risk_content', return_value={'accepted':True})
    def test_resolution_only_invalidates_integrated_and_source_cache(self, _):
        self.db.execute('CREATE TABLE strategic_workflow_runs(id,status,error)')
        self.db.execute("INSERT INTO strategic_workflow_runs VALUES ('old','complete',NULL)")
        self.db.commit()
        with patch('baseline_graph.baseline_sources',return_value=([],{})),patch('paper_graph.paper_sources',return_value=([],dict.fromkeys(['verified','needs_review','stale','pending','failed'],0))):
            before=load_integrated_graph(self.db,{})
            self.withdraw()
            after=load_integrated_graph(self.db,{})
            self.assertGreater(len(before.full_edges),len(after.full_edges))
            self.assertEqual(1,len(after.full_edges))

    def test_independent_keyword_support_survives_withdrawal(self):
        self.withdraw()
        item={'kind':'observed_keyword','run_ids':['old','new'],'evidence_ids':['shared','other'],
              'evidence':[{'id':'shared','source_record_id':'bad','workflow_run_ids':['old','new'],'document_id':'doc1'},
                          {'id':'other','source_record_id':'good','workflow_run_ids':['old'],'document_id':'doc2'}]}
        result=project_catalog(self.db,[item])[0]
        self.assertTrue(result['retrieval_eligible'])
        self.assertEqual(2,result['support_count'])
        self.assertEqual(['new'],result['evidence'][0]['workflow_run_ids'])
        self.assertEqual(['old'],result['evidence'][1]['workflow_run_ids'])
        self.assertEqual(['old','new'],item['evidence'][0]['workflow_run_ids'])

    def test_proposal_keeps_other_runs_without_rewriting_affected_run(self):
        self.withdraw()
        item={'kind':'strategic_concept','run_ids':['old','new'],'evidence_ids':['bad','good','new'],
              'evidence':[{'id':ref,'source_record_id':ref,'workflow_run_ids':[run],'document_id':ref}
                          for ref,run in [('bad','old'),('good','old'),('new','new')]]}
        result=project_catalog(self.db,[item])[0]
        self.assertTrue(result['retrieval_eligible'])
        self.assertEqual(['new'],result['evidence_ids'])
        self.assertEqual(['new'],result['run_ids'])
        self.assertEqual(1,result['support_count'])
        item['kind']='claim_relation'
        self.assertFalse(project_catalog(self.db,[item])[0]['retrieval_eligible'])
