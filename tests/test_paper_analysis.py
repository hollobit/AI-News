import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from arxiv_papers import init_papers
from paper_analysis import PaperAnalysisService,analysis_input_hash


class PaperAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'papers.db';self.services=[];self.calls=[]
        self.metadata={'title':'Safe Robotics Learning Systems','abstract':'We study robust robot policies and report benchmark results.',
                       'authors':['Researcher'],'categories':['cs.RO'],'primary_category':'cs.RO','published':'2026-01-01','updated':'2026-01-02',
                       'metadata_version':1,'doi':'','journal_ref':''}
        self.save()

    def save(self):
        with sqlite3.connect(self.path) as db:
            init_papers(db)
            db.execute("INSERT OR REPLACE INTO arxiv_papers VALUES (?,?,'fetched','','2026-01-02')",('2601.12345',json.dumps(self.metadata)))

    def tearDown(self):
        for s in self.services:
            s.close();s.thread.join(3)
        self.temp.cleanup()

    def model(self,prompt,schema):
        self.calls.append(prompt)
        data=json.loads(prompt.split('DATA:\n')[1]);ids=[e['id'] for e in data['evidence']]
        if prompt.startswith('ROLE: paper_verification'):
            return {'accepted':True,'issues':[],'checked_evidence_ids':ids,'limitations':['초록과 발췌 범위']}
        return {'summary':'저자가 보고한 로봇 연구','claims':[{'category':'problem','title':'로봇 강건성','detail':'초록은 로봇 정책의 강건성을 다룹니다.',
                   'evidence_ids':[ids[0]],'uncertainty':'전체 실험 조건 미확인'}],'limitations':['PDF 전체는 읽지 않았습니다.']}

    def service(self,model=None,fetcher=None):
        s=PaperAnalysisService(self.path,analyzer=model or self.model,enabled=True,fetcher=fetcher or (lambda url:{'status':'failed'}),extractor=lambda text:[])
        self.services.append(s);return s

    def done(self,s):
        end=time.monotonic()+5
        while time.monotonic()<end:
            r=s.get('2601.12345')
            if r['status'] in ('complete','failed','needs_review','stale','paused'):
                return r
            time.sleep(.01)
        self.fail(str(s.status()))

    def test_abstract_fallback_review_cache_and_version_staleness(self):
        s=self.service();s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['status'],'complete',r.get('error'))
        self.assertEqual(r['result']['scope'],'abstract_and_official_metadata_only')
        self.assertEqual(len(self.calls),2)
        self.assertEqual(s.submit(['2601.12345'])['cached'],['2601.12345'])
        self.metadata['metadata_version']=2;self.metadata['updated']='2026-02-01';self.save()
        self.assertTrue(s.get('2601.12345')['stale'])
        s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['result']['version'],2)

    def test_html_excerpts_are_real_bounded_observations(self):
        body='Safe Robotics Learning Systems Introduction Methods Results References '+('Research discussion '*1600)
        s=self.service(fetcher=lambda url:{'status':'fetched','text':body,'title':self.metadata['title'],'final_url':url})
        s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['result']['scope'],'abstract_and_html_excerpts')
        chunks=[e for e in r['result']['evidence'] if e['origin']=='arxiv_html_excerpt']
        self.assertEqual(len(chunks),2)
        self.assertLessEqual(sum(len(e['text']) for e in chunks),12000)

    def test_notice_html_is_not_research_body(self):
        s=self.service(fetcher=lambda url:{'status':'fetched','text':'HTML is not available '+('sorry '*1000),'title':self.metadata['title']})
        s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['result']['scope'],'abstract_and_official_metadata_only')

    def test_fabricated_citation_is_not_saved_as_verified(self):
        def bad(prompt,schema):
            result=self.model(prompt,schema)
            if 'claims' in result:result['claims'][0]['evidence_ids']=['fake']
            return result
        s=self.service(bad);s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['status'],'failed')
        self.assertIsNone(r['result'])

    def test_incomplete_review_requires_review(self):
        def incomplete(prompt,schema):
            result=self.model(prompt,schema)
            if 'checked_evidence_ids' in result:result['checked_evidence_ids']=[]
            return result
        s=self.service(incomplete);s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['status'],'needs_review')
        self.assertFalse(r['result']['verified'])

    def test_review_feedback_revision_is_bounded_and_independently_checked(self):
        audits=[]
        def revise(prompt,schema):
            result=self.model(prompt,schema)
            if prompt.startswith('ROLE: paper_verification'):
                audits.append(prompt)
                if len(audits)==1:result.update(accepted=False,issues=['실험 범위를 명시하세요.'])
            return result
        s=self.service(revise);s.submit(['2601.12345']);r=self.done(s)
        self.assertEqual(r['status'],'complete')
        self.assertEqual(len(audits),2)
        self.assertEqual(len(r['result']['revision_history']),1)
        self.assertTrue(any('검토 지적:' in p and '실험 범위를 명시하세요.' in p for p in self.calls))

    def test_unknown_paper_rejected(self):
        s=self.service()
        with self.assertRaises(ValueError):s.submit(['2601.99999'])

    def test_verification_timeout_resume_reuses_saved_draft(self):
        failed=[False]
        def once(prompt,schema):
            if prompt.startswith('ROLE: paper_verification') and not failed[0]:
                failed[0]=True;raise RuntimeError('timeout')
            return self.model(prompt,schema)
        s=self.service(once);s.submit(['2601.12345']);self.assertEqual(self.done(s)['status'],'failed')
        s.submit(['2601.12345']);self.assertEqual(self.done(s)['status'],'complete')
        self.assertEqual(sum(p.startswith('ROLE: paper_analysis') for p in self.calls),1)

if __name__=='__main__':unittest.main()
