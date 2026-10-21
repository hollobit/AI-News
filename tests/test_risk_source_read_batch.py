import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
import risk_source_state as risk


def database(path):
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT,updated_at TEXT)')
        db.execute('INSERT INTO source_excerpts VALUES (?,?,?)',('https://example.org/a',json.dumps(dict(status='fetched',title='title',text='old')),'now'))


def evidence(text='old'):
    return dict(id='same_id',origin='fetched_url_excerpt',url='https://example.org/a',title='title',text=text)


def read(db,text='old'):
    item=evidence(text)
    return risk.evidence_source_states(db,[item])[risk.evidence_index_key(item)]['status']


def test_one_build_per_read_scope_and_next_read_sees_changes(tmp_path,monkeypatch):
    path=tmp_path/'news.db';database(path);builds=[];original=risk._current_index
    def build(db):builds.append(1);return original(db)
    monkeypatch.setattr(risk,'_current_index',build)
    @risk.source_read_scope
    def query(db):return [read(db),read(db)]
    with sqlite3.connect(path) as reader,sqlite3.connect(path) as writer:
        reader.execute('BEGIN')
        assert query(reader)==['matched_current']*2 and len(builds)==1
        writer.execute('UPDATE source_excerpts SET result_json=?',(json.dumps(dict(status='fetched',title='title',text='new')),));writer.commit()
        assert query(reader)==['matched_current']*2 and len(builds)==2
        reader.rollback();reader.execute('BEGIN')
        assert query(reader)==['changed']*2 and len(builds)==3
    assert risk._READ_SCOPE.get() is None


def test_local_write_and_transaction_boundary_invalidate_reuse(tmp_path):
    path=tmp_path/'news.db';database(path)
    with sqlite3.connect(path) as reader,sqlite3.connect(path) as writer:
        @risk.source_read_scope
        def query(db):
            assert read(db)=='matched_current'
            writer.execute('UPDATE source_excerpts SET result_json=?',(json.dumps(dict(status='fetched',title='title',text='new')),));writer.commit()
            assert read(db)=='matched_current'  # Still the original read snapshot.
            db.rollback();db.execute('BEGIN')
            assert read(db)=='changed'
            db.execute('UPDATE source_excerpts SET result_json=?',(json.dumps(dict(status='failed',title='title',text='new')),))
            assert read(db)=='unavailable'
        reader.execute('BEGIN');query(reader)


def test_scope_never_crosses_connections_or_survives_exception(tmp_path):
    path=tmp_path/'news.db';database(path)
    with sqlite3.connect(path) as a,sqlite3.connect(path) as b:
        @risk.source_read_scope
        def query(db):
            assert read(db)=='matched_current'
            b.execute('UPDATE source_excerpts SET result_json=?',(json.dumps(dict(status='failed',title='title',text='old')),))
            assert read(b)=='unavailable'
            assert read(db)=='matched_current'
            raise RuntimeError('cancelled')
        a.execute('BEGIN')
        with pytest.raises(RuntimeError,match='cancelled'):query(a)
    assert risk._READ_SCOPE.get() is None


def test_concurrent_scopes_keep_their_own_snapshot(tmp_path):
    paths=[tmp_path/'one.db',tmp_path/'two.db']
    for path in paths:database(path)
    with sqlite3.connect(paths[1]) as db:db.execute('DELETE FROM source_excerpts')
    @risk.source_read_scope
    def query(db):return [read(db),read(db)]
    def worker(path):
        with sqlite3.connect(path) as db:
            db.execute('BEGIN');return query(db)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(worker,paths))
    assert results==[['matched_current']*2,['unavailable']*2]
