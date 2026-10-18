import json
import sqlite3

from reach_pipeline import ReachPipeline


def prepare(path):
    pipeline=ReachPipeline(path,autostart=False)
    pipeline.close()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO source_excerpts VALUES ('https://example.org/new',?,'stamp')",
                   (json.dumps(dict(status='fetched',fetched_at='checked',reader='reader',evidence_scope='excerpt')),))


def test_startup_only_backfills_missing_health_and_preserves_retry_state(tmp_path):
    path=tmp_path/'db';prepare(path)
    existing=('https://example.org/existing','checked','success','failed','HTTP 429','rate_limit',3,
              '2099-01-01','old-hash','old-scope','reader')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO source_health VALUES (?,?,?,?,?,?,?,?,?,?,?)',existing)
        # Already migrated legacy payloads are not parsed or rewritten at startup.
        db.execute("INSERT INTO source_excerpts VALUES (?,?,'stamp')",(existing[0],'{obsolete legacy data'))
    pipeline=ReachPipeline(path,autostart=False)
    try:
        with pipeline.db() as db:
            assert tuple(db.execute('SELECT * FROM source_health WHERE url=?',(existing[0],)).fetchone())==existing
            new=db.execute("SELECT * FROM source_health WHERE url='https://example.org/new'").fetchone()
            assert new['last_status']=='fetched' and new['last_success']=='checked' and new['failures']==0
    finally:pipeline.close()


def test_startup_reserves_writer_before_legacy_read(tmp_path,monkeypatch):
    path=tmp_path/'db';prepare(path);observed=[]

    class InterleavingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result=super().execute(sql,parameters)
            if sql.lstrip().startswith('SELECT') and 'FROM source_excerpts' in sql:
                # Attempt a competing WAL commit precisely between the legacy
                # read and health insert. Without reservation this commit wins
                # and the startup connection cannot upgrade its old snapshot.
                with sqlite3.connect(path,timeout=.02) as competing:
                    try:
                        competing.execute("UPDATE source_excerpts SET updated_at='concurrent'")
                    except sqlite3.OperationalError as error:
                        assert 'locked' in str(error)
                        observed.append('reserved')
                    else:observed.append('unprotected')
            return result

    def connect(self):
        db=sqlite3.connect(path,factory=InterleavingConnection);db.row_factory=sqlite3.Row
        return db
    monkeypatch.setattr(ReachPipeline,'db',connect)
    pipeline=ReachPipeline(path,autostart=False)
    try:
        assert observed==['reserved']
        with sqlite3.connect(path,timeout=.1) as db:
            db.execute("UPDATE source_excerpts SET updated_at='after startup'")
            assert db.execute('SELECT count(*) FROM source_health').fetchone()[0]==1
    finally:pipeline.close()
