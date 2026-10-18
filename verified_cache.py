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


from code_policy import policy


@contextmanager
def measurement():
    """Optional numeric diagnostics for one thread; never record cached content."""
    previous=getattr(_LOCAL,'metrics',None)
    metrics={name:0 for name in ('open_ms','read_ms','write_ms','cleanup_ms','hits','misses','writes','errors')}
    _LOCAL.metrics=metrics
    try:yield metrics
    finally:
        _LOCAL.metrics=previous
        for key in metrics:
            if key.endswith('_ms'):metrics[key]=round(metrics[key],2)


def _elapsed(metrics, name, started):
    if metrics is not None:metrics[name]+=(time.monotonic()-started)*1000


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
    metrics=getattr(_LOCAL,'metrics',None)
    opened=time.monotonic() if metrics is not None else 0
    try:
        if path and (writable or path.exists()):
            connection = sqlite3.connect(str(path) if writable else path.resolve().as_uri()+'?mode=ro', uri=not writable, timeout=2)
            if writable:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE IF NOT EXISTS validations(namespace TEXT,key TEXT,policy TEXT,value BLOB,created REAL,PRIMARY KEY(namespace,key,policy))')
                connection.execute('CREATE INDEX IF NOT EXISTS validation_created ON validations(created)')
            _LOCAL.store = (path, connection, writable)
    except sqlite3.Error:
        if metrics is not None:metrics['errors']+=1
        if connection: connection.close()
        connection = None
        _LOCAL.store = None
    finally:
        _elapsed(metrics,'open_ms',opened)
    try:
        yield
    finally:
        if connection:
            cleanup=time.monotonic() if metrics is not None else 0
            try:
                if writable and connection.total_changes:
                    connection.commit()
                    # Draining workers may still use the previous code policy.
                    # Versioned keys isolate them; deleting each other's entries
                    # would repeatedly force full validation during deployment.
                    # The shared byte budget below bounds all policy generations.
                    # A hit-only session cannot grow the store. Avoid a full-table
                    # scan on each read; total_changes also includes writes that
                    # were already committed by the 64-entry batching below.
                    total = connection.execute('SELECT COALESCE(SUM(LENGTH(value)),0) FROM validations').fetchone()[0]
                    if total > MAX_BYTES:
                        for namespace,key,version,size in connection.execute('SELECT namespace,key,policy,LENGTH(value) FROM validations ORDER BY created').fetchall():
                            connection.execute('DELETE FROM validations WHERE namespace=? AND key=? AND policy=?',(namespace,key,version))
                            total -= size
                            if total <= MAX_BYTES: break
                    connection.commit()
            except sqlite3.Error:
                if metrics is not None:metrics['errors']+=1
                pass  # Disposable cache failure must not hide a valid result.
            finally:
                connection.close()
                _elapsed(metrics,'cleanup_ms',cleanup)
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
    metrics=getattr(_LOCAL,'metrics',None)
    started=time.monotonic() if metrics is not None else 0
    try:
        row = connection.execute('SELECT value FROM validations WHERE namespace=? AND key=? AND policy=?',(namespace,str(key),policy())).fetchone()
        if row:
            value=json.loads(zlib.decompress(row[0]))
            if metrics is not None:metrics['hits']+=1
            return value, True
    except (sqlite3.Error, ValueError, zlib.error):
        if metrics is not None:metrics['errors']+=1
        pass
    finally:
        _elapsed(metrics,'read_ms',started)
    if metrics is not None:metrics['misses']+=1
    result = builder()
    if writable:
        body = zlib.compress(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),1)
        if len(body) <= 4 * 1024 * 1024:
            started=time.monotonic() if metrics is not None else 0
            try:
                connection.execute('INSERT OR REPLACE INTO validations VALUES(?,?,?,?,?)',(namespace,str(key),policy(),body,time.time()))
                if metrics is not None:metrics['writes']+=1
                # Bound lock tenure during full archive builds.
                if connection.total_changes % 64 == 0: connection.commit()
            except sqlite3.Error:
                if metrics is not None:metrics['errors']+=1
                pass
            finally:
                _elapsed(metrics,'write_ms',started)
    return result, False
