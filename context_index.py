"""Search admitted sources before constructing a question's connected graph.

Immutable, revision-bound SQLite indexes use the same admission and graph merge
rules as the full map. Only shortlisted records need Python tokenization and
relationship construction. The full graph remains available on the map page.
"""
from collections import OrderedDict
from concurrent.futures import Future
import json
import re
import sqlite3
import threading
import hashlib
from pathlib import Path
import shutil
from contextlib import closing

from graph_disk_index import Store, encode
from task_lifecycle import checkpoint

VERSION = 'workspace-sources-v2-incremental'
KINDS = ('graph', 'research', 'workflow', 'paper', 'baseline', 'external', 'wiki')


def wiki_revision(db):
    """Published interpretations/configuration can change without editing news."""
    from projection_cache import content_digest
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    records = []
    if {'wiki_topics', 'wiki_revisions'} <= tables:
        records.extend(tuple(r) for r in db.execute('''SELECT t.id,t.published_revision,r.result_json
            FROM wiki_topics t LEFT JOIN wiki_revisions r ON r.id=t.published_revision ORDER BY t.id'''))
    if 'wiki_settings' in tables:
        records.extend(tuple(r) for r in db.execute('SELECT * FROM wiki_settings ORDER BY id'))
    return content_digest(records)


def previous_index(directory):
    """A disposable base may be obsolete; every source is re-admitted on update."""
    for path in sorted(Path(directory).glob('*.sqlite3'), key=lambda p:p.stat().st_mtime, reverse=True):
        try:
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as store:
                row=store.execute("SELECT body FROM meta WHERE key='workspace_format'").fetchone()
                if row and row[0]==VERSION:return path
        except sqlite3.Error:continue
    return None


def write(path, db, key, previous=None):
    from graph_rag import _analysis_sources
    from integrated_context import enrich_sources
    sources, coverage = _analysis_sources(db)
    # Risk/scenario materialization uses this immutable payload, never a newer
    # final artifact mixed with an older source index.
    finals = {r[0]: r[1] for r in db.execute("SELECT run_id,payload_json FROM strategic_workflow_artifacts WHERE stage='final'")} if db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_artifacts'").fetchone() else {}
    sources = sources + enrich_sources(db, [])
    if previous:
        clone_index(previous, path)
    with sqlite3.connect(path) as store:
        # This is a disposable, unpublished copy. Failure deletes it; rollback
        # journals only duplicate the large index and can outlive a killed child.
        store.execute('PRAGMA journal_mode=OFF')
        if not previous:store.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE sources(id INTEGER PRIMARY KEY,kind TEXT,body TEXT,title TEXT,text TEXT,source_key TEXT UNIQUE,digest TEXT);
            CREATE INDEX source_kind ON sources(kind);
            CREATE VIRTUAL TABLE search USING fts5(title,text,content='sources',content_rowid='id',tokenize='trigram');''')
        existing={r[0]:(r[1],r[2]) for r in store.execute('SELECT source_key,id,digest FROM sources')}
        store.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',
                          [('key',key),('coverage',encode(coverage)),('workspace_format',VERSION),('servable','1')])
        changed=0
        def unindex(identity):
            row=store.execute('SELECT title,text FROM sources WHERE id=?',(identity,)).fetchone()
            store.execute("INSERT INTO search(search,rowid,title,text) VALUES('delete',?,?,?)",(identity,*row))
        for source in sources:
            checkpoint()
            source = dict(source)
            result = dict(source['result'])
            # Graph sets have no presentation order. Stable serialization avoids
            # reindexing thousands of identical graphs in a fresh Python process.
            for field in ('nodes','edges','evidence'):
                if field in result:result[field]=sorted(result[field],key=lambda item:
                    str(item.get('id') or json.dumps(item,ensure_ascii=False,sort_keys=True)))
            source['result']=result
            title = ' '.join(n.get('name', '') for n in result.get('nodes', []))
            title += ' ' + ' '.join(e.get('title', '') for e in result.get('evidence', []))
            text = ' '.join(n.get('summary', '') for n in result.get('nodes', []))
            text += ' ' + ' '.join(e.get('text', '') for e in result.get('evidence', []))
            if source['kind'] == 'workflow' and source['id'] in finals:
                payload = json.loads(finals[source['id']])
                source['workspace_final'] = {k: payload[k] for k in ('risk_report', 'risk_verification', 'risk_verified', 'evidence') if k in payload}
                # Candidate matching only; the original risk gate still runs
                # before a risk or scenario can enter the materialized graph.
                text += ' ' + encode(payload.get('risk_report', {}))
            body=encode(source)
            source_key=encode([source['kind'],source['id'],source.get('row',{}).get('run_id')])
            digest=hashlib.sha256(body.encode()).hexdigest()
            old=existing.pop(source_key,None)
            if old and old[1]==digest:continue
            changed+=1
            update_search=True
            if old:
                previous_text=store.execute('SELECT title,text FROM sources WHERE id=?',(old[0],)).fetchone()
                update_search=previous_text!=(title,text)
                if update_search:unindex(old[0])
                store.execute('UPDATE sources SET kind=?,body=?,title=?,text=?,digest=? WHERE id=?',
                              (source['kind'],body,title,text,digest,old[0]))
                identity=old[0]
            else:
                identity=store.execute('INSERT INTO sources(kind,body,title,text,source_key,digest) VALUES(?,?,?,?,?,?)',
                                      (source['kind'],body,title,text,source_key,digest)).lastrowid
            if previous and update_search:store.execute('INSERT INTO search(rowid,title,text) VALUES(?,?,?)',(identity,title,text))
        for identity,_ in existing.values():
            unindex(identity)
            store.execute('DELETE FROM sources WHERE id=?',(identity,))
        if not previous:store.execute("INSERT INTO search(search) VALUES('rebuild')")
        stats=dict(mode='incremental' if previous else 'full',sources=len(sources),changed=changed,removed=len(existing))
        store.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('build',encode(stats)))
    return stats


def clone_index(previous, path):
    """Copy-on-write on supported filesystems, portable byte-copy fallback.

    Never hard-link: the unpublished generation will be modified independently.
    """
    import sys
    import os
    if Path(path).exists():raise FileExistsError(path)
    try:
        if sys.platform=='darwin':
            import ctypes
            libc=ctypes.CDLL(None,use_errno=True)
            clone=libc.clonefile
            clone.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int]
            clone.restype=ctypes.c_int
            if clone(os.fsencode(previous),os.fsencode(path),0):raise OSError(ctypes.get_errno(),'clonefile')
            return
        if sys.platform.startswith('linux'):
            import fcntl
            with open(previous,'rb') as source,open(path,'xb') as target:
                fcntl.ioctl(target.fileno(),0x40049409,source.fileno())  # FICLONE
            return
    except (OSError,AttributeError):
        Path(path).unlink(missing_ok=True)
    shutil.copyfile(previous,path)


def read(path, key):
    from graph_rag import GraphResult
    store = Store(path)
    metadata=dict(store.rows("SELECT key,body FROM meta WHERE key IN ('key','servable')"))
    if metadata.get('key') != key or metadata.get('servable')=='0':
        raise ValueError('Obsolete workspace index')
    coverage = json.loads(store.rows("SELECT body FROM meta WHERE key='coverage'")[0][0])
    graph = GraphResult(nodes=[], edges=[], evidence=[], coverage=coverage)
    graph.search_key = key
    graph.workspace_store = store
    graph.workspace_cache = OrderedDict()
    graph.workspace_flights = {}
    graph.workspace_lock = threading.RLock()
    return graph


def expressions(question):
    from evidence_search import terms, alias_rules
    tokens = set(terms(question))
    words = {t for t in tokens if not t.startswith('concept:')}
    for label, _, literals in alias_rules():
        if 'concept:' + label in tokens:
            words.update(literals)
    words = sorted(words, key=lambda word: (-len(word), word))[:32]
    long = [w for w in words if len(w) >= 3]
    short = [w for w in words if len(w) == 2 and re.search('[가-힣]', w)]
    return ' OR '.join('"' + w.replace('"', '""') + '"' for w in long), short


def materialize(graph, question):
    return materialize_many(graph,[question])


def materialize_many(graph, questions):
    """Reserve candidates per query/kind, build their shared graph only once."""
    questions=tuple(dict.fromkeys(questions))
    question=questions
    with graph.workspace_lock:
        if question in graph.workspace_cache:
            graph.workspace_cache.move_to_end(question)
            return graph.workspace_cache[question]
        owner = question not in graph.workspace_flights
        future = graph.workspace_flights.setdefault(question, Future())
    if not owner: return future.result()
    try:
        result = _materialize(graph, questions)
        with graph.workspace_lock:
            graph.workspace_cache[question] = result
            while len(graph.workspace_cache)>1 and (len(graph.workspace_cache)>8 or
                    sum(g['coverage'].get('selected_source_records',0) for g in graph.workspace_cache.values())>1200):
                graph.workspace_cache.popitem(last=False)
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with graph.workspace_lock: graph.workspace_flights.pop(question, None)


def _materialize(graph, questions):
    from graph_rag import merge_sources
    from graph_retrieval import RetrievalIndex
    from integrated_context import enrich_sources
    from projection_cache import content_digest
    selected = {}
    # One connection/SQLite page cache for the query batch; fetch large source
    # bodies only once, after selecting the union of per-axis candidate IDs.
    with closing(sqlite3.connect(Path(graph.workspace_store.path).as_uri()+'?mode=ro',uri=True,timeout=2)) as store:
        identities=set()
        for question in questions:
            expression, short = expressions(question)
            for kind in KINDS:
                checkpoint()
                if expression:
                    identities.update(r[0] for r in store.execute('''SELECT s.id FROM search JOIN sources s ON s.id=search.rowid
                        WHERE search MATCH ? AND s.kind=? ORDER BY bm25(search,3,1),s.id LIMIT 48''',(expression,kind)))
                if short:
                    clauses=' OR '.join('(title LIKE ? OR text LIKE ?)' for _ in short)
                    args=[v for word in short for v in ('%'+word+'%','%'+word+'%')]
                    identities.update(r[0] for r in store.execute(
                        'SELECT id FROM sources WHERE kind=? AND ('+clauses+') ORDER BY id LIMIT 16',[kind]+args))
        ordered=sorted(identities)
        for offset in range(0,len(ordered),400):
            batch=ordered[offset:offset+400]
            selected.update(store.execute('SELECT id,body FROM sources WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
    sources = [json.loads(body) for _, body in sorted(selected.items())]
    # Reuse enrichment against only the immutable selected workflow artifacts.
    # Wiki entries already have their original source links in this generation.
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json)')
        for source in sources:
            if 'workspace_final' in source:
                db.execute('INSERT INTO strategic_workflow_artifacts VALUES(?,?,?)',
                           (source['id'], 'final', encode(source.pop('workspace_final'))))
        sources = enrich_sources(db, sources)
        result = merge_sources(db, sources, graph['coverage'], {})
    result['coverage'].update(scope='질문별 관련 자료의 연결 분석', selected_source_records=len(sources))
    result['limitations'][0] = '분야별 검색 상위 후보의 인용·관계를 종합합니다. 자료 전체를 전수 독해하거나 모든 관계를 열거한 결과가 아닙니다.'
    result.search_key = content_digest([graph.search_key, questions, VERSION,'query-batch-v1'])
    result.prepared_index = RetrievalIndex(result)
    return result
