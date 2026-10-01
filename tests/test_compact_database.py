import sqlite3
from compact_database import compact,identities


def test_compaction_preserves_rowids_history_and_exact_backup(tmp_path):
    path=tmp_path/'news.sqlite';backup=tmp_path/'original.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE history(id TEXT PRIMARY KEY,body TEXT)')
        db.executemany('INSERT INTO history(rowid,id,body) VALUES(?,?,?)',[(2,'a','first'),(9,'b','last')])
        db.execute('CREATE TABLE free_space(id INTEGER PRIMARY KEY,body TEXT)')
        db.execute('INSERT INTO free_space VALUES(1,?)',('x'*100000,));db.commit()
        db.execute('DELETE FROM free_space');db.commit()
        expected=identities(db)
    db.close()
    result=compact(path,backup)
    assert result['bytes_after']<result['bytes_before']
    for file in (path,backup):
        with sqlite3.connect(file) as db:assert identities(db)==expected
