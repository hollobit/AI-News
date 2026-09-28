"""Content/rule keyed local features; never stores or admits model analyses."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from functools import lru_cache


@lru_cache(maxsize=1)
def rule_version():
    root=Path(__file__).resolve().parent
    return hashlib.sha256(b''.join((root/name).read_bytes() for name in ('sector_taxonomy.py','strategic_value.py'))).hexdigest()


def feature_key(item):
    source=item.get('source_context') or {}
    # Exactly the evidence fields used by the two lexical classifiers.
    value=[item.get(k) for k in ('title','text','excerpt','abstract')]+[source.get(k) for k in ('status','title','text')]
    return hashlib.sha256(json.dumps(value,ensure_ascii=False).encode()).hexdigest()


def store_path(db):
    path=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'), '')
    return Path(path+'.features.sqlite3') if path else None


@contextmanager
def cached_features(db):
    path=store_path(db);connection=None
    if path and path.exists():
        try:connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
        except sqlite3.OperationalError:pass
    try:yield connection
    finally:
        if connection:connection.close()


def read(connection,item):
    if connection is None:return None
    try:
        row=connection.execute('SELECT value FROM document_features WHERE input_hash=? AND rule_version=?',(feature_key(item),rule_version())).fetchone()
        value=json.loads(row[0]) if row else None
        return value if isinstance(value,dict) and isinstance(value.get('sectors'),list) and isinstance(value.get('strategic_value'),dict) else None
    except (sqlite3.OperationalError,ValueError):return None


def persist(db,items):
    """Background-only persistence into a separate expendable cache database."""
    path=store_path(db)
    if not path:return
    with sqlite3.connect(path,timeout=2) as store:
        store.execute('PRAGMA journal_mode=WAL')
        store.execute('CREATE TABLE IF NOT EXISTS document_features(input_hash TEXT,rule_version TEXT,value TEXT NOT NULL,PRIMARY KEY(input_hash,rule_version))')
        rows=[]
        for item in items:
            value={'sectors':item['sectors'],'strategic_value':item['strategic_value']}
            rows.append((feature_key(item),rule_version(),json.dumps(value,ensure_ascii=False)))
        store.executemany('INSERT OR IGNORE INTO document_features VALUES (?,?,?)',rows)
