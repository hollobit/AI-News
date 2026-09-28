import json,sqlite3,time
from arxiv_papers import init_papers
from paper_pipeline import PaperPipeline
from paper_context import retrieve,current,strategic_context
import test_paper_graph as fixtures


def test_verified_papers_retrieved_and_stale_analysis_removed():
    fixture=fixtures.PaperGraphTests();fixture.setUp()
    try:
        fixture.add()
        evidence,deps=retrieve(fixture.db,'Physical AI robot experiment')
        assert evidence and deps and current(fixture.db,deps)
        assert any(e['source_kind']=='reviewed_paper_interpretation' for e in evidence)
        context=strategic_context(fixture.db)
        assert any(t['id']=='physical' for t in context['topics'])
        assert not retrieve(fixture.db,'totally unrelated finance')[0]
        fixture.db.execute("UPDATE arxiv_paper_analyses SET status='needs_review'")
        assert not current(fixture.db,deps)
        assert not retrieve(fixture.db,'Physical AI robot experiment')[0]
    finally:fixture.tearDown()


def services(tmp_path,cooldown=0):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        init_papers(db)
        db.execute('CREATE TABLE arxiv_paper_analyses(paper_id TEXT,input_hash TEXT,status TEXT)')
        db.execute("INSERT INTO arxiv_papers VALUES ('2609.12345','{}','pending','','')")
    class Metadata:
        active=None
        calls=[]
        def cooldown_until(self,db):return cooldown
        def refresh(self,ids,limit):self.calls.append(ids);return {'id':'job1','paper_ids':ids}
    class Analysis:
        enabled=True
        calls=[]
        def submit(self,ids,limit):self.calls.append(ids);return {'queued':ids}
    m,a=Metadata(),Analysis()
    pipeline=PaperPipeline(path,m,a,autostart=False)
    from types import SimpleNamespace
    pipeline.alternates=SimpleNamespace(tick=lambda:None,status=lambda:[])
    return pipeline,m,a


def test_cooldown_blocks_collection_but_fetched_paper_analysis_can_proceed(tmp_path):
    pipeline,m,a=services(tmp_path,time.time()+3600)
    pipeline.tick()
    assert not m.calls and not a.calls
    assert pipeline.status()['next_retry_at']
    with pipeline.db() as db:db.execute("UPDATE arxiv_papers SET status='fetched'")
    pipeline.tick()
    assert a.calls==[['2609.12345']] and not m.calls
    pipeline.configure(False);assert not pipeline.status()['enabled']
    pipeline.close()


def test_three_metadata_failures_pause_and_resume_preserves_cooldown(tmp_path):
    pipeline,m,a=services(tmp_path)
    with pipeline.db() as db:
        db.execute("INSERT INTO arxiv_metadata_jobs VALUES('bad','failed','[]','','429',0)")
        db.execute("UPDATE paper_pipeline_state SET failures=2,last_job='bad'")
    pipeline.tick();assert pipeline.status()['official_api_paused'] and pipeline.status()['enabled']
    retry=pipeline.status()['next_at']
    pipeline.configure(True)
    assert pipeline.status()['enabled'] and pipeline.status()['next_at']==retry
    pipeline.tick();assert not m.calls
    pipeline.close()


def test_scheduler_survives_locked_error_recording(tmp_path):
    from unittest.mock import patch
    pipeline,_,_=services(tmp_path)
    calls=[]
    def tick():
        calls.append(True)
        if len(calls)==1:raise sqlite3.OperationalError('database is locked')
        pipeline.stop.set()
    with patch.object(pipeline,'tick',side_effect=tick), \
         patch.object(pipeline,'db',side_effect=sqlite3.OperationalError('database is locked')), \
         patch.object(pipeline.stop,'wait',return_value=False):
        pipeline._loop()
    assert len(calls)==2
    pipeline.close()
