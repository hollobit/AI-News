"""Real child dispatch ordering; no network, model calls or production DB writes."""
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
import pipeline_wakeup

ROOT=Path(pipeline_wakeup.__file__).resolve().parent


def script(root,name,body):
    (root/name).write_text(body)


def test_coordinator_waits_for_committed_deep_owner_before_source_window(tmp_path):
    from datetime import datetime,timezone
    stamp=datetime.now(timezone.utc).isoformat()
    db=sqlite3.connect(tmp_path/'news.sqlite3')
    db.executescript('''CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE risk_schedule(id,enabled,launched_pid,cycle_id);
        CREATE TABLE rsi_cycles(id,owner_pid,pause_requested,created_at,status,error);
        CREATE TABLE bulk_baseline_runs(id,status,created_at);
        CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json,updated_at);''')
    db.executemany('INSERT INTO state VALUES(?,?)',[
        ('collector_last_success',stamp),
        ('collector_corpus_snapshot',json.dumps({'status':'complete','extracted_at':stamp}))])
    db.execute("INSERT INTO risk_schedule VALUES(1,1,NULL,'deep')")
    db.execute("INSERT INTO rsi_cycles VALUES('deep',NULL,0,?,'complete','')",(stamp,))
    db.commit();db.close()
    script(tmp_path,'scheduled_risks.py','''import os,sqlite3,time
from pathlib import Path
time.sleep(.05)
with sqlite3.connect('news.sqlite3') as db:
    db.execute('UPDATE risk_schedule SET launched_pid=?',(os.getppid(),))
Path('deep-committed').write_text('ready')
''')
    script(tmp_path,'scheduled_collection.py',f'''import json,sqlite3,sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
from baseline_source_window import wait_for_sources
from bulk_baseline import freeze_item
with sqlite3.connect('news.sqlite3') as db:
    db.row_factory=sqlite3.Row
    snapshot=freeze_item(dict(title='AI 도구 발표',text='AI 도구 발표 https://example.org/a',source_url='https://example.org/a'))
    extraction=json.loads(db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()[0])
    stamp=datetime.fromisoformat(extraction['extracted_at']);clock=[0.0]
    def sleep(seconds):
        clock[0]+=seconds
        db.execute('INSERT INTO source_excerpts VALUES(?,?,?)',('https://example.org/a',json.dumps({{'status':'fetched','text':'확보한 원문의 실제 작동 조건'}}),stamp.isoformat()))
    result=wait_for_sources(db,{{snapshot['document_id']:snapshot}},{{snapshot['document_id']}},{{}},extraction,stamp,clock=lambda:clock[0],sleep=sleep)
    Path('baseline.json').write_text(json.dumps({{'owner_registered':Path('deep-committed').exists(),'source_window':result}}))
''')
    # Before registration the original simultaneous route can skip coalescing.
    subprocess.run([sys.executable,str(tmp_path/'scheduled_collection.py')],cwd=tmp_path,check=True)
    before=json.loads((tmp_path/'baseline.json').read_text())
    assert before=={'owner_registered':False,'source_window':None}
    assert pipeline_wakeup.coordinate(tmp_path,risks=True)==0
    after=json.loads((tmp_path/'baseline.json').read_text())
    assert after['owner_registered'] is True
    assert after['source_window']['reason']=='sources_ready'
    assert after['source_window']['ready_documents']==1
    assert after['source_window']['elapsed_seconds']==1


@pytest.mark.parametrize('failure',['exit','spawn'])
def test_deep_failure_does_not_suppress_baseline_admission(tmp_path,monkeypatch,failure):
    script(tmp_path,'scheduled_risks.py','raise SystemExit(7)')
    script(tmp_path,'scheduled_collection.py',"from pathlib import Path\nPath('baseline-ran').touch()")
    original=subprocess.Popen
    if failure=='spawn':
        def launch(command,**kwargs):
            if command[1].endswith('scheduled_risks.py'):raise OSError('unavailable')
            return original(command,**kwargs)
        monkeypatch.setattr(pipeline_wakeup.subprocess,'Popen',launch)
    assert pipeline_wakeup.coordinate(tmp_path,risks=True)==(7 if failure=='exit' else 1)
    assert (tmp_path/'baseline-ran').exists()


def test_baseline_handoff_does_not_dispatch_risks(tmp_path):
    script(tmp_path,'scheduled_risks.py',"from pathlib import Path\nPath('unexpected-risk').touch()")
    script(tmp_path,'scheduled_collection.py',"from pathlib import Path\nPath('baseline-ran').touch()")
    assert pipeline_wakeup.coordinate(tmp_path,risks=False)==0
    assert (tmp_path/'baseline-ran').exists() and not (tmp_path/'unexpected-risk').exists()


@pytest.mark.parametrize('risks',[False,True])
def test_notify_detaches_coordinator_without_waiting_and_rejects_test_database(monkeypatch,tmp_path,risks):
    spawn=Mock();thread=Mock();monkeypatch.setattr(pipeline_wakeup.subprocess,'Popen',spawn)
    monkeypatch.setattr(pipeline_wakeup.threading,'Thread',thread)
    pipeline_wakeup.notify(tmp_path/'news.sqlite3',risks=risks);spawn.assert_not_called()
    pipeline_wakeup.notify(ROOT/'data/news.sqlite3',risks=risks)
    command=spawn.call_args.args[0]
    assert command==[sys.executable,str(ROOT/'pipeline_wakeup.py')]+(['--risks'] if risks else [])
    assert spawn.call_args.kwargs['start_new_session'] is True
    spawn.return_value.wait.assert_not_called()
    assert thread.call_args.kwargs['target']==spawn.return_value.wait
    thread.return_value.start.assert_called_once()


def test_coordinated_risk_check_waits_for_an_existing_scheduler_lock(tmp_path):
    (tmp_path/'.runtime').mkdir()
    wrapper=tmp_path/'risk_check.py'
    wrapper.write_text(f'''import sys
from pathlib import Path
sys.path.insert(0,{str(ROOT)!r})
import app,scheduled_risks
app.load_local_env=lambda:None
scheduled_risks.ROOT=Path({str(tmp_path)!r})
def dispatch(*args,**kwargs):
    Path('admitted').touch()
    return {{'stage':'running'}}
scheduled_risks.dispatch=dispatch
sys.argv=['scheduled_risks.py','--wait-for-check']
Path('entered').touch()
scheduled_risks.main()
''')
    child=None
    try:
        with (tmp_path/'.runtime/scheduled-risks.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            child=subprocess.Popen([sys.executable,str(wrapper)],cwd=tmp_path,stdout=subprocess.DEVNULL)
            deadline=time.monotonic()+10
            while not (tmp_path/'entered').exists() and time.monotonic()<deadline:
                assert child.poll() is None
                time.sleep(.01)
            assert (tmp_path/'entered').exists()
            time.sleep(.05)
            assert child.poll() is None and not (tmp_path/'admitted').exists()
        assert child.wait(timeout=10)==0
        assert (tmp_path/'admitted').exists()
    finally:
        if child and child.poll() is None:
            child.terminate();child.wait(timeout=10)
