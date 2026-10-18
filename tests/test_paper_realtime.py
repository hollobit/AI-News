import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

from arxiv_papers import init_papers, paper
from paper_analysis import PaperAnalysisService, analysis_input_hash
from paper_pipeline import PaperPipeline
from paper_recovery import setup, eligible, reserve, failed, MAX_RECOVERIES


def populate(path, count=1):
    with sqlite3.connect(path) as db:
        init_papers(db)
        for i in range(count):
            item=dict(title=f'Robot experiment {i}',abstract=f'We report robot experiment {i}.',
                      authors=[],categories=['cs.RO'],primary_category='cs.RO',published='2026-10-01',updated='2026-10-01',metadata_version=1)
            db.execute("INSERT INTO arxiv_papers VALUES (?,?,'fetched','','2026-10-01')",
                       (f'2610.{10000+i}',json.dumps(item)))


def test_single_paper_read_ignores_unrelated_bodies_and_is_read_only(tmp_path):
    path=tmp_path/'news';populate(path,2)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE arxiv_papers SET metadata_json='invalid unrelated json' WHERE paper_id='2610.10001'")
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        queries=[];db.set_trace_callback(queries.append)
        assert paper(db,'2610.10000')['title']=='Robot experiment 0'
        assert not any(q.startswith(('CREATE','INSERT','UPDATE')) for q in queries)


def test_papers_run_in_parallel_without_double_claiming(tmp_path):
    path=tmp_path/'news';populate(path,3)
    gate=threading.Event();entered=[];lock=threading.Lock()
    def analyze(prompt,schema):
        data=json.loads(prompt.split('DATA:\n')[1]);ref=data['evidence'][0]['id']
        if prompt.startswith('ROLE: paper_analysis'):
            with lock:entered.append(data['paper_id'])
            gate.wait(5)
            return dict(summary='저자가 보고한 실험',claims=[dict(category='problem',title='실험',detail='저자가 로봇 실험을 보고했다.',evidence_ids=[ref],uncertainty='초록 범위')],limitations=['초록만 확인'])
        return dict(accepted=True,issues=[],checked_evidence_ids=[ref],limitations=['초록 범위'])
    service=PaperAnalysisService(path,analyzer=analyze,enabled=True,fetcher=lambda _:dict(status='failed'),extractor=lambda _:[],workers=2)
    try:
        service.submit(['2610.10000','2610.10001','2610.10002'])
        until=time.monotonic()+3
        while len(entered)<2 and time.monotonic()<until:time.sleep(.01)
        assert len(entered)==2 and len(set(entered))==2
        status=service.status();assert status['workers']==2 and len(status['running_ids'])==2
        assert status['counts']['queued']==1
        gate.set()
        until=time.monotonic()+4
        while service.status()['counts'].get('complete',0)<3 and time.monotonic()<until:time.sleep(.01)
        assert service.status()['counts']['complete']==3 and len(set(entered))==3
    finally:
        gate.set();service.close();service.thread.join(5)


def test_infrastructure_recovery_has_its_own_monotonic_bounded_history(tmp_path):
    from engine_errors import EngineError
    with sqlite3.connect(tmp_path/'db') as db:
        setup(db)
        row=dict(paper_id='2610.10000',input_hash='same',status='failed',error=str(EngineError('circuit_open')),updated_at='2026-01-01T00:00:00+00:00')
        stamp=time.time()
        for i in range(MAX_RECOVERIES):
            assert reserve(db,row,stamp+(i+1)*10000)
            assert not eligible(db,row,stamp+(i+1)*10000)
        assert not reserve(db,row,stamp+100000)
        failed(db,row,EngineError('network'))
        assert db.execute('SELECT attempts FROM paper_analysis_recovery').fetchone()[0]==3
        assert db.execute('SELECT count(*) FROM paper_analysis_events').fetchone()[0]==4
        assert not eligible(db,dict(row,input_hash='new',error='잘못된 인용'),stamp)
        assert not eligible(db,dict(row,input_hash='new',error=str(EngineError('rate_limit'))),stamp)


def fixture_pipeline(tmp_path):
    from types import SimpleNamespace
    path=tmp_path/'news';populate(path)
    analysis=PaperAnalysisService(path,enabled=True,start_worker=False,workers=4)
    metadata=SimpleNamespace(active=None,cooldown_until=lambda _:time.time()+3600)
    pipeline=PaperPipeline(path,metadata,analysis,autostart=False,engine_ready=lambda:True)
    pipeline.alternates=SimpleNamespace(tick=lambda:None,status=lambda:[])
    return pipeline,analysis


def test_recovery_requeues_checkpoint_without_resetting_old_attempts(tmp_path):
    from engine_errors import EngineError
    pipeline,analysis=fixture_pipeline(tmp_path)
    try:
        analysis.submit(['2610.10000'])
        with pipeline.db() as db:
            db.execute("UPDATE arxiv_paper_analyses SET status='failed',error=?,updated_at='2026-01-01',evidence_json='checkpoint'",(str(EngineError('circuit_open')),))
            db.execute("INSERT INTO paper_pipeline_attempts VALUES('2610.10000','analysis',2,0)")
        pipeline.tick()
        with pipeline.db() as db:
            row=db.execute('SELECT status,evidence_json FROM arxiv_paper_analyses').fetchone()
            assert tuple(row)==('queued','checkpoint')
            assert db.execute('SELECT attempts FROM paper_pipeline_attempts').fetchone()[0]==3
            assert db.execute('SELECT attempts FROM paper_analysis_recovery').fetchone()[0]==1
            db.execute('UPDATE paper_pipeline_state SET failures=3')
        pipeline.configure(False);pipeline.configure(True)
        with pipeline.db() as db:
            assert db.execute('SELECT attempts FROM paper_pipeline_attempts').fetchone()[0]==3
            assert db.execute('SELECT failures FROM paper_pipeline_state').fetchone()[0]==3
    finally:pipeline.close();analysis.close()


def test_open_engine_circuit_does_not_consume_paper_retries(tmp_path):
    pipeline,analysis=fixture_pipeline(tmp_path)
    pipeline.engine_ready=lambda:False
    try:
        pipeline.tick()
        with pipeline.db() as db:
            assert not db.execute('SELECT 1 FROM arxiv_paper_analyses').fetchone()
            assert not db.execute('SELECT 1 FROM paper_pipeline_attempts').fetchone()
    finally:pipeline.close();analysis.close()


def test_user_pipeline_pause_keeps_queued_checkpoints_unclaimed(tmp_path):
    pipeline,analysis=fixture_pipeline(tmp_path)
    try:
        analysis.submit(['2610.10000'])
        pipeline.configure(False)
        assert analysis._claim() is None
        assert analysis.status()['counts']['queued']==1
        pipeline.configure(True)
        assert analysis._claim()['paper_id']=='2610.10000'
    finally:pipeline.close();analysis.close()


def test_slow_metadata_provider_cannot_hold_ready_analysis(tmp_path):
    pipeline,analysis=fixture_pipeline(tmp_path)
    gate=threading.Event();entered=threading.Event()
    pipeline.autostart=True
    def slow():entered.set();gate.wait(5)
    pipeline.alternates.tick=slow
    pipeline.provider_thread.start()
    try:
        assert entered.wait(2)
        start=time.monotonic();pipeline.tick()
        assert time.monotonic()-start<1
        assert analysis.status()['counts']['queued']==1
    finally:gate.set();pipeline.close();analysis.close()
