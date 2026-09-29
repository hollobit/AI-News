"""Disposable per-document projections, persisted only by background readers.

A current input/rule hash is required for every reuse. This cache is never a
publication or model-review gate; removing it only causes recomputation.
"""
from task_lifecycle import checkpoint
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import sqlite3
from document_features import store_path
from projection_cache import content_digest, is_read_projection


@lru_cache(maxsize=32)
def rules_digest(files):
    root = Path(__file__).resolve().parent
    return hashlib.sha256(b''.join((root / name).read_bytes() for name in files)).hexdigest()


@contextmanager
def reader(db):
    path = store_path(db)
    connection = None
    if path and path.exists():
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        except sqlite3.OperationalError:
            pass
    try:
        yield connection
    finally:
        if connection:
            connection.close()


def document_rows(db, namespace, inputs, build):
    """Batch-build only missing/changed documents; prune removed rows on writes."""
    keys = {key: content_digest(value) for key, value in inputs.items()}
    reused = {}
    with reader(db) as store:
        if store:
            try:
                for key, signature, body in store.execute(
                        'SELECT document_id,input_hash,value FROM document_projections WHERE namespace=?', (namespace,)):
                    checkpoint()
                    if keys.get(key) == signature:
                        value = json.loads(body)
                        if isinstance(value, dict):
                            reused[key] = value
            except (sqlite3.OperationalError, ValueError):
                reused = {}
    missing = [key for key in inputs if key not in reused]
    computed = build(missing) if missing else {}
    if set(computed) != set(missing) or any(not isinstance(v, dict) for v in computed.values()):
        raise ValueError('Incomplete document projection')
    path = store_path(db)
    if path and not is_read_projection():
        with sqlite3.connect(path, timeout=15) as store:
            store.execute('PRAGMA journal_mode=WAL')
            store.execute('''CREATE TABLE IF NOT EXISTS document_projections (
                namespace TEXT,document_id TEXT,input_hash TEXT NOT NULL,value TEXT NOT NULL,
                PRIMARY KEY(namespace,document_id))''')
            store.execute('''CREATE TABLE IF NOT EXISTS document_projection_changes (
                seq INTEGER PRIMARY KEY,namespace TEXT,document_id TEXT,input_hash TEXT,kind TEXT)''')
            for key, value in computed.items():
                checkpoint()
                store.execute('''INSERT INTO document_projections VALUES(?,?,?,?)
                    ON CONFLICT(namespace,document_id) DO UPDATE SET input_hash=excluded.input_hash,value=excluded.value''',
                    (namespace, key, keys[key], json.dumps(value, ensure_ascii=False)))
                store.execute('INSERT INTO document_projection_changes(namespace,document_id,input_hash,kind) VALUES(?,?,?,?)',
                              (namespace, key, keys[key], 'upsert'))
            removed = [r[0] for r in store.execute('SELECT document_id FROM document_projections WHERE namespace=?', (namespace,)) if r[0] not in inputs]
            for key in removed:
                checkpoint()
                store.execute('DELETE FROM document_projections WHERE namespace=? AND document_id=?', (namespace, key))
                store.execute('INSERT INTO document_projection_changes(namespace,document_id,kind) VALUES(?,?,?)', (namespace, key, 'delete'))
            store.execute('DELETE FROM document_projection_changes WHERE seq < (SELECT COALESCE(MAX(seq),0)-10000 FROM document_projection_changes)')
    result = {**reused, **computed}
    return {key: result[key] for key in inputs}
