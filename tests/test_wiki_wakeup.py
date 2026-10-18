import sqlite3
import threading
from types import SimpleNamespace
import pytest
from wiki_wakeup import InputWatch, install, notify, status_for_view


class Clock:
    value = 0

    def __call__(self):
        return self.value


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'news.db'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE articles(id INTEGER PRIMARY KEY,title,text,day);
        INSERT INTO articles VALUES(1,'title','text','2026-10-19');
        CREATE TABLE source_health(url PRIMARY KEY,hash,last_status,last_checked,failures);
        INSERT INTO source_health VALUES('url','current','fetched',0,0);
        CREATE TABLE source_versions(url,hash,title,text,fetched_at);
        INSERT INTO source_versions VALUES('url','current','title','text',0);
        INSERT INTO source_versions VALUES('url','old','old title','old text',0);
        CREATE TABLE arxiv_papers(paper_id PRIMARY KEY,metadata_json,status,fetched_at);
        INSERT INTO arxiv_papers VALUES('paper','{}','fetched',0);
        CREATE TABLE arxiv_paper_analyses(paper_id,input_hash,status,error,result_json,owner_pid,updated_at,draft_json);
        INSERT INTO arxiv_paper_analyses VALUES('paper','hash','complete','','{}',NULL,0,'{}');
        CREATE TABLE wiki_topics(status,requested,attempts);
        INSERT INTO wiki_topics VALUES('needs_review',0,2);
        ''')
        install(db)
    return path


def revision(db):
    return db.execute('SELECT epoch,revision FROM wiki_input_revision').fetchone()


@pytest.mark.parametrize('statement', [
    "UPDATE articles SET title='corrected'", "UPDATE articles SET day='2026-10-20'",
    "INSERT INTO articles VALUES(2,'new','new','2026-10-19')", 'DELETE FROM articles',
    "UPDATE source_health SET hash='new'", "UPDATE source_health SET last_status='failed'",
    'DELETE FROM source_health', "UPDATE source_versions SET text='correction' WHERE hash='current'",
    "DELETE FROM source_versions WHERE hash='current'", "UPDATE arxiv_papers SET metadata_json='{\"v\":2}'",
    'DELETE FROM arxiv_papers', "UPDATE arxiv_paper_analyses SET status='needs_review'",
    "UPDATE arxiv_paper_analyses SET input_hash='new'", "UPDATE arxiv_paper_analyses SET result_json='{\"review\":false}'",
    "UPDATE arxiv_paper_analyses SET error='retracted'", 'DELETE FROM arxiv_paper_analyses',
])
def test_changes_to_bound_inputs_are_detected(store, statement):
    with sqlite3.connect(store) as db:
        before = revision(db)
        db.execute(statement)
        assert revision(db) != before


@pytest.mark.parametrize('statement', [
    'UPDATE articles SET title=title', 'UPDATE source_health SET last_checked=9,failures=3',
    'UPDATE source_versions SET fetched_at=9', "DELETE FROM source_versions WHERE hash='old'",
    "UPDATE source_versions SET text='irrelevant' WHERE hash='old'",
    'UPDATE arxiv_papers SET fetched_at=9',
    "UPDATE arxiv_paper_analyses SET owner_pid=123,updated_at=9,draft_json='{\"checkpoint\":1}'",
    "UPDATE wiki_topics SET status='complete',requested=1,attempts=3",
])
def test_operational_updates_and_old_source_retention_do_not_trigger(store, statement):
    with sqlite3.connect(store) as db:
        before = revision(db)
        db.execute(statement)
        assert revision(db) == before


def test_transaction_visibility_rollback_and_constant_size_poll(store):
    clock = Clock(); watch = InputWatch(clock=clock, wall=clock)
    with sqlite3.connect(store) as reader, sqlite3.connect(store) as writer:
        assert not watch.poll(reader)
        reader.commit()  # Initial trigger installation belongs to the caller transaction.
        before = revision(reader)
        writer.execute("UPDATE articles SET text='rolled back'")
        assert not watch.poll(reader) and revision(reader) == before
        writer.rollback()
        assert not watch.poll(reader)
        writer.execute("UPDATE articles SET text='committed'"); writer.commit()
        assert not watch.poll(reader) and watch.status()['state'] == 'coalescing'
        clock.value = 20
        trace = []; reader.set_trace_callback(trace.append)
        assert watch.poll(reader)
        assert len(trace) == 2 and all('articles' not in q for q in trace)
        assert not watch.poll(reader)


def test_install_is_idempotent_and_schema_change_recovers(store):
    clock = Clock(); watch = InputWatch(clock=clock, wall=clock)
    with sqlite3.connect(store) as db:
        watch.poll(db)
        before = revision(db), db.execute('PRAGMA schema_version').fetchone()
        install(db)
        assert (revision(db), db.execute('PRAGMA schema_version').fetchone()) == before
        db.execute('DROP TABLE articles')
        db.execute('CREATE TABLE articles(id,title,text,day,new_field)')
        db.execute("INSERT INTO articles VALUES(1,'new','new','now','new')")
        assert not watch.poll(db)
        clock.value = 10
        assert watch.poll(db)
        old = revision(db)
        db.execute("UPDATE articles SET new_field='changed'")
        assert revision(db) != old
        db.execute('DROP TABLE wiki_input_revision')
        assert not watch.poll(db) and revision(db)[0] != old[0]
        clock.value = 30
        assert watch.poll(db)


def test_bursts_coalesce_but_continuous_input_cannot_postpone_forever(store):
    clock = Clock(); watch = InputWatch(clock=clock, wall=clock)
    with sqlite3.connect(store) as db:
        watch.poll(db)
        for second in range(0, 46, 5):
            clock.value = second
            db.execute('UPDATE articles SET text=?', (str(second),))
            assert watch.poll(db) is (second == 45)
        assert watch.status()['notifications'] == 1
        clock.value = 50
        db.execute("UPDATE articles SET text='last'")
        assert not watch.poll(db)
        clock.value = 69
        assert not watch.poll(db) and watch.status()['remaining_seconds'] == 1
        clock.value = 70
        assert watch.poll(db)
        clock.value = 100
        assert not watch.poll(db) and watch.status()['notifications'] == 2


def test_source_only_update_uses_ten_seconds_but_article_waits_for_original(store):
    clock = Clock(); watch = InputWatch(clock=clock, wall=clock)
    with sqlite3.connect(store) as db:
        watch.poll(db)
        db.execute("UPDATE source_versions SET text='corrected' WHERE hash='current'")
        assert not watch.poll(db)
        clock.value = 10
        assert watch.poll(db)
        db.execute("UPDATE articles SET text='new Telegram message'")
        assert not watch.poll(db)
        clock.value = 20
        assert not watch.poll(db) and watch.status()['remaining_seconds'] == 10
        clock.value = 28
        db.execute("UPDATE source_versions SET text='new original' WHERE hash='current'")
        assert not watch.poll(db)
        clock.value = 30
        assert not watch.poll(db)
        clock.value = 38
        assert watch.poll(db) and watch.status()['notifications'] == 2


def test_disabled_error_recovery_and_notification_preserve_jobs(store):
    clock = Clock(); watch = InputWatch(clock=clock, wall=clock)
    wiki = SimpleNamespace(enabled=False, wake=threading.Event())
    with sqlite3.connect(store) as db:
        notify(db, watch, wiki)
        db.execute("UPDATE articles SET text='disabled change'")
        clock.value = 50; notify(db, watch, wiki)
        assert not wiki.wake.is_set() and watch.status()['state'] == 'disabled'
        wiki.enabled = True
        notify(db, watch, wiki)
        db.execute("UPDATE articles SET text='enabled change'")
        notify(db, watch, wiki)
        broken = sqlite3.connect(':memory:'); broken.close()
        clock.value = 70
        assert not watch.poll(broken)
        assert watch.status()['error_type'] == 'ProgrammingError'
        notify(db, watch, wiki)
        assert wiki.wake.is_set() and watch.status()['error_type'] is None
        assert db.execute('SELECT * FROM wiki_topics').fetchall() == [('needs_review', 0, 2)]


def test_stale_worker_diagnostics_never_look_live():
    value = dict(state='coalescing', remaining_seconds=3, checked_at=123, secret='hidden')
    result = status_for_view(dict(status='running',health='unresponsive',wiki_source_updates=value))
    assert result['state'] == 'unavailable' and 'checked_at' not in result and 'secret' not in result
    assert status_for_view(dict(status='stopped')) is None


def test_real_wiki_loop_wakes_for_changes_during_work_and_while_idle(tmp_path):
    from test_knowledge_wiki import setup, Runner
    from knowledge_wiki import KnowledgeWiki
    path = tmp_path / 'news.db'; setup(path)
    runner = Runner(); wiki = KnowledgeWiki(path, runner, enabled=True, start_worker=False)
    clock = Clock(); watch = InputWatch(clock=clock, wall=clock)
    from wiki_sources import TOPICS
    for topic in TOPICS:
        if topic != 'medical':
            wiki.manage(dict(topic=topic, enabled=False))
    with wiki.db() as db:
        watch.poll(db)
    entered = threading.Event(); release = threading.Event(); finished = threading.Event()
    original = wiki.compile
    sweeps = []
    def compile_topic(topic):
        if not sweeps:
            entered.set()
            assert release.wait(5)
        sweeps.append(original(topic))
        if len(sweeps) >= 2:
            finished.set()
    wiki.compile = compile_topic
    wiki.thread = threading.Thread(target=wiki._loop)
    wiki.thread.start()
    try:
        assert entered.wait(5)
        # This notification must survive until the worker finishes its first sweep.
        with wiki.db() as db:
            db.execute("UPDATE articles SET excerpt='변경된 연구 결과'")
        with wiki.db() as db: notify(db, watch, wiki)
        clock.value = 20
        with wiki.db() as db: notify(db, watch, wiki)
        release.set()
        assert finished.wait(5)
        assert len(runner.calls) == 2  # The second sweep reuses the same reviewed input.
        finished.clear()
        with wiki.db() as db:
            db.execute("UPDATE articles SET excerpt='추가 정정 결과'")
        with wiki.db() as db: notify(db, watch, wiki)
        clock.value = 40
        with wiki.db() as db: notify(db, watch, wiki)
        assert finished.wait(5)
        assert sweeps == ['complete', None, 'complete']
        assert len(runner.calls) == 4 and wiki.get('medical')['page']
    finally:
        release.set(); wiki.close(); wiki.thread.join(5)
        assert not wiki.thread.is_alive()


def test_wakeup_counter_alone_does_not_request_publication(store, tmp_path):
    from publication_state import signature
    before = signature(store, tmp_path)
    with sqlite3.connect(store) as db:
        db.execute('UPDATE wiki_input_revision SET revision=revision+1')
    assert signature(store, tmp_path) == before

