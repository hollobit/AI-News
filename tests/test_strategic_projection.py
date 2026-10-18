import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import CancelledError
from unittest.mock import patch

import pytest

from app import connect, process_updates
from projection_cache import revision_token
from strategic_hub import StrategicHub
from task_lifecycle import cancellation_scope


def archive(path):
    with connect(path) as db:
        revision_token(db)
        process_updates(db, [{'update_id':1, 'channel_post':{
            'chat':{'id':-100123,'type':'channel'}, 'message_id':1,
            'date':1789531200, 'text':'AI 안전성 연구 결과\nhttps://example.org/safety'}}], {'-100123'})
    return StrategicHub(path, start_worker=False, enabled=False)


def state(hub):
    with hub.db() as db:
        return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ('intel_entities','intel_dependencies','intel_history',
                              'intel_assessments','intel_records','intel_record_history',
                              'intel_risk_history','intel_analysis_jobs')}


def test_real_child_preserves_sources_review_history_and_model_job_owner(tmp_path):
    hub=archive(tmp_path/'news.sqlite')
    try:
        hub.sync()
        doc=hub.get('documents')['items'][0]
        hub.mutate('decisions', {'action':'create','title':'검토 선택','question':'도입할까',
            'evidence_ids':[doc['id']], 'options':[{'id':'wait','label':'추가 검토'}],
            'selected_option_id':'wait','rationale':'현재 자료 검토'})
        hub.sync()  # Apply the normal first refresh of the new analyst record.
        with hub.db() as db:
            # A projection must never perform StrategicJobs' orphan recovery.
            db.execute('INSERT INTO intel_analysis_jobs VALUES (?,?,?,?,?,?,?,?,?)',
                ('pending','query','running','{}','{}','','2026-10-18','2026-10-18',99999999))
        before=state(hub)
        hub.last_token=None  # Force the real subprocess through the full path.
        result=hub._background_sync()
        assert result['status']=='ready' and hub.last_token
        assert state(hub)==before
        assert not hub.jobs.futures
        # No-change refresh still checks analyst records without rebuilding sources.
        with hub.db() as db, patch('strategic_projection.sync_store',side_effect=AssertionError('full refresh')):
            from strategic_projection import refresh
            token,runtime=refresh(db,previous_token=hub.last_token,runtime=result)
            assert token==hub.last_token and runtime==result
        assert hub.sync()==result
    finally:
        hub.close()


def test_child_source_change_marks_existing_decision_for_review(tmp_path):
    hub=archive(tmp_path/'news.sqlite')
    try:
        hub._background_sync()
        doc=hub.get('documents')['items'][0]
        decision=hub.mutate('decisions', {'action':'create','title':'도입 검토','question':'도입할까',
            'evidence_ids':[doc['id']], 'options':[{'id':'wait','label':'추가 검토'}],
            'selected_option_id':'wait','rationale':'현재 자료 검토'})['item']
        previous_token=hub.last_token
        with hub.db() as db:
            db.execute("UPDATE articles SET title=title || ' 정정' ")
        hub._background_sync()
        updated=hub.get('decisions',{'id':decision['id']})['item']
        assert hub.last_token!=previous_token
        assert updated['review_state']=='needs_review'
        assert updated['selected_option_id']=='wait'
        assert updated['version']>decision['version']
        with hub.db() as db:
            assert db.execute('SELECT count(*) FROM intel_record_history WHERE id=?',(decision['id'],)).fetchone()[0]>=2
    finally:
        hub.close()


def test_failed_child_does_not_advance_success_or_revision(tmp_path):
    hub=archive(tmp_path/'news.sqlite')
    try:
        hub._background_sync()
        before=state(hub);previous=hub.last_token;success=hub.runtime['last_success']
        with patch('projection_worker.prepare',side_effect=RuntimeError('child failed')):
            with pytest.raises(RuntimeError):hub._background_sync()
        assert hub.last_token==previous and hub.runtime['last_success']==success
        assert state(hub)==before
        assert hub._background_sync()['status']=='ready'
    finally:
        hub.close()


def test_cancelled_projection_terminates_real_child_and_rolls_back(tmp_path):
    path=tmp_path/'child.sqlite'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE marker(value INTEGER)')
    # Hold an actual uncommitted transaction until the parent's cancellation check.
    code="""import sqlite3,sys,time
db=sqlite3.connect(sys.argv[1]);db.execute('INSERT INTO marker VALUES (1)')
print('ready',flush=True);time.sleep(30)
"""
    child=subprocess.Popen([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        stopped=threading.Event()
        def cancel():stopped.set();raise CancelledError()
        from projection_worker import prepare
        with cancellation_scope(stopped), patch('projection_worker.subprocess.Popen',return_value=child), patch('projection_worker.checkpoint',side_effect=cancel):
            with pytest.raises(CancelledError):prepare('intelligence',str(path),'')
            assert child.poll() is not None
            with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM marker').fetchone()[0]==0
            stopped.clear()
    finally:
        if child.poll() is None:child.kill();child.wait()
        child.stdout.close()
