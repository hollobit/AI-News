import copy
import json
import sqlite3
import unittest

from arxiv_papers import init_papers, paper
from graph_rag import load_integrated_graph
from paper_analysis import analysis_input_hash, digest
from paper_graph import paper_coverage


class PaperGraphTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        init_papers(self.db)
        self.db.execute('CREATE TABLE arxiv_paper_analyses(paper_id TEXT PRIMARY KEY,input_hash TEXT,status TEXT,result_json TEXT,error TEXT)')

    def tearDown(self):
        self.db.close()

    def add(self, identity='2609.12345', status='complete', mutate=None):
        metadata = {'paper_id': identity, 'title': 'Physical AI', 'abstract': 'The authors report a robot experiment.',
                    'authors': ['Author'], 'categories': ['cs.RO'], 'primary_category': 'cs.RO',
                    'published': '2026-09-10T00:00:00Z', 'updated': '2026-09-12T00:00:00Z',
                    'metadata_version': 2, 'doi': '', 'journal_ref': ''}
        self.db.execute("INSERT INTO arxiv_papers VALUES (?,?,'fetched','','')",(identity,json.dumps(metadata)))
        item=paper(self.db, identity)
        signature=analysis_input_hash(item)
        evidence=[{'id': 'abstract1', 'paper_id': identity, 'version': 2, 'origin': 'arxiv_abstract',
                   'source_url': 'https://arxiv.org/abs/'+identity+'v2', 'published': metadata['published'],
                   'title': metadata['title'], 'text': metadata['abstract']}]
        report={'summary': '저자가 실험을 보고함', 'claims': [{'title': '로봇 실험', 'detail': '저자가 로봇 실험을 보고했다.',
                'category': 'reported_result', 'evidence_ids': ['abstract1'], 'uncertainty': '초록에 한정'}], 'limitations': ['전체 논문 미확인']}
        result={'paper_id':identity,'version':2,'input_hash':signature,'verified':True,'report':report,
                'evidence':evidence,'keywords':[], 'verification': {'accepted':True,'issues':[],
                'checked_evidence_ids':['abstract1'],'report_hash':digest(report),'evidence_hash':digest(evidence)}}
        if mutate:
            mutate(result)
        self.db.execute('INSERT INTO arxiv_paper_analyses VALUES (?,?,?,?,?)',
                        (identity,signature,status,json.dumps(result),''))
        return result

    def test_verified_paper_claims_add_citations_with_publication_date(self):
        self.add()
        graph=load_integrated_graph(self.db,{})
        self.assertEqual(paper_coverage(self.db)['verified'],1)
        self.assertEqual(graph['coverage']['completed_verified_papers'],1)
        self.assertEqual(graph['edges'][0]['relation'],'근거 인용')
        self.assertEqual(graph['edges'][0]['confidence'],'reviewed_interpretation')
        self.assertEqual(graph['edges'][0]['support_count'],1)
        source=graph['evidence'][0]
        self.assertTrue(source['id'].startswith('paper:'))
        self.assertEqual(source['day'],'2026-09-10')
        self.assertEqual(source['paper_id'],'2609.12345')
        self.assertEqual(source['evidence_origin'],'arxiv_abstract')
        self.assertEqual(source['version'],2)
        self.assertTrue(load_integrated_graph(self.db,{'date':['2026-09-10']})['edges'])

    def test_stale_metadata_and_review_rejections_are_not_graph_evidence(self):
        self.add('2609.10001')
        metadata=json.loads(self.db.execute("SELECT metadata_json FROM arxiv_papers WHERE paper_id='2609.10001'").fetchone()[0])
        metadata['abstract']='Changed official abstract'
        self.db.execute("UPDATE arxiv_papers SET metadata_json=? WHERE paper_id='2609.10001'",(json.dumps(metadata),))
        self.add('2609.10002',status='needs_review')
        self.add('2609.10003',mutate=lambda result: result['verification'].update(accepted=False))
        self.assertEqual(load_integrated_graph(self.db,{})['edges'],[])
        counts=paper_coverage(self.db)
        self.assertEqual(counts['stale'],1)
        self.assertEqual(counts['needs_review'],2)

    def test_wrong_paper_urls_origins_unchecked_and_changed_content_fail(self):
        mutations=[lambda r:r['evidence'][0].update(source_url='https://arxiv.org/abs/2609.99999v2'),
                   lambda r:r['evidence'][0].update(paper_id='2609.99999'),
                   lambda r:r['evidence'][0].update(origin='mirofish_simulation'),
                   lambda r:r['evidence'][0].update(source_url='2609.12345'),
                   lambda r:r['verification'].update(checked_evidence_ids=[])]
        for index, mutation in enumerate(mutations):
            def resign(result, mutation=mutation):
                mutation(result)
                result['verification']['evidence_hash']=digest(result['evidence'])
            self.add(f'2609.{10000+index}',mutate=resign)
        self.add('2609.20000',mutate=lambda r:r['report'].update(summary='감사 후 변경'))
        self.assertEqual(paper_coverage(self.db)['verified'],0)
        self.assertEqual(load_integrated_graph(self.db,{})['evidence'],[])

    def test_global_evidence_ids_do_not_collide_across_papers(self):
        self.add('2609.11111')
        self.add('2609.22222')
        graph=load_integrated_graph(self.db,{})
        self.assertEqual(len({e['id'] for e in graph['evidence']}),2)
        self.assertEqual(graph['coverage']['analyzed_unique_documents'],2)


if __name__=='__main__':
    unittest.main()
