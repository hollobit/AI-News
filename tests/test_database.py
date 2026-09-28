from database import open_db
from app import connect


def test_plain_connection_does_not_initialize_or_classify(tmp_path):
    path=tmp_path/'news.db'
    with open_db(path) as db:
        assert db.execute('SELECT name FROM sqlite_master').fetchall()==[]
    with connect(path) as db:
        schema=db.execute('PRAGMA schema_version').fetchone()[0]
        changes=db.total_changes
        assert db.execute('SELECT version FROM schema_migrations').fetchone()[0]==1
    with open_db(path,read_only=True) as db:
        assert db.execute('PRAGMA schema_version').fetchone()[0]==schema
        assert db.total_changes==0
        assert db.execute('SELECT COUNT(*) FROM news').fetchone()[0]==0


def test_http_revision_lookup_never_prepares_schema(tmp_path):
    from projection_cache import read_projections, revision_token
    with open_db(tmp_path/'news.db') as db:
        db.execute('CREATE TABLE articles(id INTEGER PRIMARY KEY,text TEXT)')
        db.commit()
        schema = db.execute('PRAGMA schema_version').fetchone()[0]
        with read_projections():
            assert revision_token(db) is None
        assert db.execute('PRAGMA schema_version').fetchone()[0] == schema
        assert db.total_changes == 0
        prepared = revision_token(db)
        changes = db.total_changes
        with read_projections():
            assert revision_token(db) == prepared
        assert db.total_changes == changes
        db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT)')
        db.commit()
        with read_projections():
            assert revision_token(db) is None


def test_reading_morphology_never_creates_or_writes_cache(tmp_path):
    from morphology import keyword_records
    from projection_cache import read_projections
    item = {'chat_id': '1', 'message_id': 1, 'item_index': 0, 'title': 'AI model', 'text': 'AI model learning'}
    with open_db(tmp_path/'news.db') as db:
        schema = db.execute('PRAGMA schema_version').fetchone()[0]
        with read_projections():
            records = keyword_records(db, [item])
        assert records
        assert db.total_changes == 0
        assert db.execute('PRAGMA schema_version').fetchone()[0] == schema
        assert not db.in_transaction
        prepared = keyword_records(db, [item])
        changes = db.total_changes
        with read_projections():
            assert keyword_records(db, [item]) == prepared
        assert db.total_changes == changes
