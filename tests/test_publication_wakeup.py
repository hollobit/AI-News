import json
import sqlite3

import pytest

from publication_wakeup import Changes, Schedule, install


@pytest.fixture
def store(tmp_path):
    path=tmp_path/'news.db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE articles(id TEXT,text TEXT);
            CREATE TABLE arxiv_paper_analyses(paper_id TEXT,input_hash TEXT,status TEXT,error TEXT,result_json TEXT,updated_at REAL,owner_pid INTEGER,draft_json TEXT);
            CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT,updated_at REAL);
            CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT);
            CREATE TABLE wiki_topics(id TEXT,status TEXT,published_revision TEXT,updated_at REAL,owner_pid INTEGER,requested INTEGER);
            CREATE TABLE source_health(url TEXT,hash TEXT,last_status TEXT);
            CREATE TABLE source_versions(url TEXT,hash TEXT,title TEXT,text TEXT);
            CREATE TABLE paper_analysis_checkpoints(paper_id TEXT,state_json TEXT);
            INSERT INTO arxiv_paper_analyses VALUES('p','h','complete','','{}',0,NULL,NULL);
            INSERT INTO wiki_topics VALUES('t','complete','r',0,NULL,0);''')
        install(db)
    return path


def revision(db):
    return db.execute('SELECT revision FROM publication_input_revision').fetchone()[0]


def test_approved_content_and_revocation_wake_but_progress_and_noops_do_not(store):
    with sqlite3.connect(store) as db:
        start=revision(db)
        db.execute('UPDATE arxiv_paper_analyses SET updated_at=42,owner_pid=123,draft_json=?',('draft',))
        db.execute('UPDATE arxiv_paper_analyses SET result_json=result_json')
        db.execute('UPDATE wiki_topics SET updated_at=42,owner_pid=123,requested=1')
        db.execute("INSERT INTO paper_analysis_checkpoints VALUES('p','checkpoint')")
        assert revision(db)==start
        db.execute("UPDATE arxiv_paper_analyses SET result_json='changed'")
        assert revision(db)==start+1
        db.execute("UPDATE arxiv_paper_analyses SET status='needs_review'")
        assert revision(db)==start+2
        db.execute("UPDATE wiki_topics SET published_revision='new'")
        assert revision(db)==start+3


def test_partial_workflow_is_not_a_publication_event_but_approval_and_withdrawal_are(store):
    with sqlite3.connect(store) as db:
        start=revision(db)
        db.execute("INSERT INTO strategic_workflow_runs VALUES('r','running','',0)")
        db.execute("INSERT INTO strategic_workflow_artifacts VALUES('r','draft','{}')")
        db.execute('UPDATE strategic_workflow_runs SET updated_at=1')
        assert revision(db)==start
        db.execute("INSERT INTO strategic_workflow_artifacts VALUES('r','final','{}')")
        db.execute("UPDATE strategic_workflow_runs SET status='complete'")
        assert revision(db)==start+2
        db.execute("UPDATE strategic_workflow_runs SET error='approval withdrawn'")
        assert revision(db)==start+3
        db.execute('DELETE FROM strategic_workflow_artifacts WHERE stage=?',('final',))
        assert revision(db)==start+4


def test_old_source_history_is_ignored_until_it_becomes_current(store):
    with sqlite3.connect(store) as db:
        start=revision(db)
        db.execute("INSERT INTO source_versions VALUES('url','h','title','text')")
        assert revision(db)==start
        db.execute("INSERT INTO source_health VALUES('url','h','fetched')")
        assert revision(db)==start+1
        db.execute("UPDATE source_versions SET text='new'")
        assert revision(db)==start+2


def test_rolled_back_changes_are_not_observed_and_commits_are(store,tmp_path):
    watch=Changes(store,tmp_path);first=watch.read()
    with sqlite3.connect(store) as writer:
        writer.execute("INSERT INTO articles VALUES('a','new')")
        assert watch.read()==first
        writer.rollback()
    assert watch.read()==first
    with sqlite3.connect(store) as writer:writer.execute("INSERT INTO articles VALUES('a','new')")
    assert watch.read()!=first


def test_schema_recreation_and_files_wake_without_reading_credentials(store,tmp_path):
    watch=Changes(store,tmp_path);first=watch.read()
    with sqlite3.connect(store) as db:
        db.execute('DROP TABLE articles');db.execute('CREATE TABLE articles(id TEXT,text TEXT)')
    assert watch.read()!=first
    first=watch.read();static=tmp_path/'static';static.mkdir();(static/'public.js').write_text('new')
    assert watch.read()!=first
    first=watch.read();runtime=tmp_path/'.runtime/remote-access';runtime.mkdir(parents=True)
    (runtime/'password.txt').write_text('not watched')
    assert watch.read()==first
    (runtime/'public.json').write_text('{"origin":"https://example.org"}')
    assert watch.read()!=first


def test_wakeup_counter_does_not_trigger_conservative_publication_preflight(store,tmp_path):
    from publication_state import signature
    before=signature(store,tmp_path)
    with sqlite3.connect(store) as db:db.execute('UPDATE publication_input_revision SET revision=revision+1')
    assert signature(store,tmp_path)==before


def clocked():
    now=[0.];return Schedule(clock=lambda:now[0]),now


def test_initial_fallback_quiet_window_and_one_active_child():
    s,t=clocked();s.observe('a');assert s.due();s.started();assert not s.due();s.finished(True)
    t[0]=5;s.observe('b');assert not s.due()
    t[0]=14;assert not s.due()
    t[0]=15;assert s.due();s.started();assert not s.due();s.finished(True)
    t[0]=314;assert not s.due()
    t[0]=315;assert s.due()


def test_continuous_updates_have_maximum_wait_and_updates_during_export_survive():
    s,t=clocked();s.observe('a');s.started();s.finished(True)
    for n in range(5,50,5):
        t[0]=n;s.observe(n);assert not s.due()
    t[0]=50;assert s.due();s.started()
    t[0]=51;s.observe('during export')
    t[0]=80;assert not s.due();s.finished(True);assert s.due()


def test_failed_publication_preserves_changes_and_waits_before_retry():
    s,t=clocked();s.observe('a');s.started()
    t[0]=5;s.observe('b');s.finished(False)
    t[0]=15;assert not s.due() and s.view()['state']=='retry_wait'
    t[0]=304;assert not s.due()
    t[0]=305;assert s.due()


def test_watch_drains_one_child_on_shutdown_and_retains_pending_changes(monkeypatch,tmp_path):
    import publication_wakeup as module
    t=[0.];started=[];states=[]
    class Stop:
        def is_set(self):return t[0]>=2
        def wait(self,seconds):t[0]+=seconds
    class Child:
        pid=123;returncode=None
        def __init__(self,*a,**kw):started.append(t[0])
        def poll(self):
            if t[0]>=4:self.returncode=0
            return self.returncode
    cls=Schedule
    monkeypatch.setattr(module,'Schedule',lambda:cls(clock=lambda:t[0]))
    monkeypatch.setattr(module.Changes,'read',lambda _:'a' if t[0]<2 else 'b')
    monkeypatch.setattr('subprocess.Popen',Child)
    monkeypatch.setattr('time.sleep',lambda seconds:t.__setitem__(0,t[0]+seconds))
    monkeypatch.setattr('worker_health.write_state',lambda path,value:states.append(value))
    module.watch(tmp_path/'db',tmp_path/'site',stop=Stop())
    assert started==[0.] and states[-1]['child_pid'] is None
    assert states[-1]['stopping'] and states[-1]['state']=='coalescing'
