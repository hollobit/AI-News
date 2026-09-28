import sqlite3
import unittest
from unittest.mock import patch
from strategic_records import init_records
from strategic_research import milestone,research_view


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');init_records(self.db)
        self.docs={'d':{'id':'d','version':1,'status':'current'}}
        self.papers=[{'paper_id':pid,'title':'paper','abstract':'abstract','metadata_status':'fetched','analysis_status':'pending','source_url':'https://arxiv.org/abs/'+pid} for pid in ['2609.00001','2609.00002']]
        self.patches=[patch('strategic_research._paper_rows',return_value=self.papers),patch('strategic_research._rows',return_value=[]),patch('strategic_research.validated_paper_analysis',return_value={})]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
        self.db.close()

    def test_publication_is_not_adoption(self):
        row=research_view(self.db,{},self.docs.get)['items'][0]
        self.assertEqual('observed',row['stages'][0]['status'])
        self.assertEqual({'unknown'},{s['status'] for s in row['stages'][1:]})

    def test_milestone_requires_source_and_becomes_stale(self):
        with self.assertRaises(ValueError):milestone(self.db,{'paper_id':'2609.00001','stage':'deployment','note':'claim'},self.docs.get)
        milestone(self.db,{'paper_id':'2609.00001','stage':'pilot','note':'explicit evidence','evidence_ids':['d']},self.docs.get)
        self.docs['d']['version']=2
        row=research_view(self.db,{'id':'2609.00001'},self.docs.get)['item']
        self.assertEqual('needs_review',next(s for s in row['stages'] if s['stage']=='pilot')['status'])
        other=research_view(self.db,{'id':'2609.00002'},self.docs.get)
        self.assertEqual([],other['history'])
