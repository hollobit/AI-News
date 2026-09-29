"""Disposable JSON validation results bound to exact inputs and code policy."""
from contextlib import contextmanager
from functools import lru_cache, wraps
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import zlib

_LOCAL = threading.local()
NAMESPACES = {'baseline_verified_content','paper_verified_content','workflow_verified_graph'}
MAX_BYTES = 256 * 1024 * 1024


@lru_cache(maxsize=1)
def policy():
    # Conservatively invalidate on any Python policy/dependency change. This is
    # computed once per process, never once per document.
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob('*.py')):
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def path_for(db):
    path = next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'), '')
    return Path(path + '.validated.sqlite3') if path else None


@contextmanager
def session(db):
    from projection_cache import is_read_projection
    path = path_for(db)
    existing = getattr(_LOCAL, 'store', None)
    if existing and existing[0] == path:
        yield
        return
    connection = None
    writable = not is_read_projection()
    try:
        if path and (writable or path.exists()):
            connection = sqlite3.connect(str(path) if writable else path.resolve().as_uri()+'?mode=ro', uri=not writable, timeout=2)
            if writable:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE IF NOT EXISTS validations(namespace TEXT,key TEXT,policy TEXT,value BLOB,created REAL,PRIMARY KEY(namespace,key,policy))')
                connection.execute('CREATE INDEX IF NOT EXISTS validation_created ON validations(created)')
            _LOCAL.store = (path, connection, writable)
    except sqlite3.Error:
        if connection: connection.close()
        connection = None
        _LOCAL.store = None
    try:
        yield
    finally:
        if connection:
            try:
                if writable:
                    connection.commit()
                    # Old policy revisions are expendable, not review history.
                    connection.execute('DELETE FROM validations WHERE policy<>?', (policy(),))
                    total = connection.execute('SELECT COALESCE(SUM(LENGTH(value)),0) FROM validations').fetchone()[0]
                    if total > MAX_BYTES:
                        for namespace,key,version,size in connection.execute('SELECT namespace,key,policy,LENGTH(value) FROM validations ORDER BY created').fetchall():
                            connection.execute('DELETE FROM validations WHERE namespace=? AND key=? AND policy=?',(namespace,key,version))
                            total -= size
                            if total <= MAX_BYTES: break
                    connection.commit()
            except sqlite3.Error:
                pass  # Disposable cache failure must not hide a valid result.
            finally:
                connection.close()
        _LOCAL.store = existing


def scoped(fn):
    @wraps(fn)
    def wrapped(db, *args, **kwargs):
        with session(db):
            return fn(db, *args, **kwargs)
    return wrapped


def read(db, namespace, key, builder):
    store = getattr(_LOCAL, 'store', None)
    if not store or store[0] != path_for(db):
        with session(db):
            if getattr(_LOCAL,'store',None):
                return read(db,namespace,key,builder)
            return builder(), False
    _, connection, writable = store
    try:
        row = connection.execute('SELECT value FROM validations WHERE namespace=? AND key=? AND policy=?',(namespace,str(key),policy())).fetchone()
        if row:
            return json.loads(zlib.decompress(row[0])), True
    except (sqlite3.Error, ValueError, zlib.error):
        pass
    result = builder()
    if writable:
        body = zlib.compress(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),1)
        if len(body) <= 4 * 1024 * 1024:
            try:
                connection.execute('INSERT OR REPLACE INTO validations VALUES(?,?,?,?,?)',(namespace,str(key),policy(),body,time.time()))
                # Bound lock tenure during full archive builds.
                if connection.total_changes % 64 == 0: connection.commit()
            except sqlite3.Error:
                pass
    return result, False
