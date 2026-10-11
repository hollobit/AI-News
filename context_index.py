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

from graph_disk_index import Store, encode
from task_lifecycle import checkpoint

VERSION = 'workspace-sources-v1'
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


def write(path, db, key):
    from graph_rag import _analysis_sources
    from integrated_context import enrich_sources
    sources, coverage = _analysis_sources(db)
    # Risk/scenario materialization uses this immutable payload, never a newer
    # final artifact mixed with an older source index.
    finals = {r[0]: r[1] for r in db.execute("SELECT run_id,payload_json FROM strategic_workflow_artifacts WHERE stage='final'")} if db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_artifacts'").fetchone() else {}
    sources = sources + enrich_sources(db, [])
    with sqlite3.connect(path) as store:
        store.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE sources(id INTEGER PRIMARY KEY,kind TEXT,body TEXT,title TEXT,text TEXT);
            CREATE INDEX source_kind ON sources(kind);
            CREATE VIRTUAL TABLE search USING fts5(title,text,content='sources',content_rowid='id',tokenize='trigram');''')
        store.execute('INSERT INTO meta VALUES(?,?)', ('key', key))
        store.execute('INSERT INTO meta VALUES(?,?)', ('coverage', encode(coverage)))
        for source in sources:
            checkpoint()
            source = dict(source)
            result = source['result']
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
            store.execute('INSERT INTO sources(kind,body,title,text) VALUES(?,?,?,?)',
                          (source['kind'], encode(source), title, text))
        store.execute("INSERT INTO search(search) VALUES('rebuild')")


def read(path, key):
    from graph_rag import GraphResult
    store = Store(path)
    if store.rows("SELECT body FROM meta WHERE key='key'")[0][0] != key:
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
    with graph.workspace_lock:
        if question in graph.workspace_cache:
            graph.workspace_cache.move_to_end(question)
            return graph.workspace_cache[question]
        owner = question not in graph.workspace_flights
        future = graph.workspace_flights.setdefault(question, Future())
    if not owner: return future.result()
    try:
        result = _materialize(graph, question)
        with graph.workspace_lock:
            graph.workspace_cache[question] = result
            while len(graph.workspace_cache) > 8:
                graph.workspace_cache.popitem(last=False)
        future.set_result(result)
        return result
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with graph.workspace_lock: graph.workspace_flights.pop(question, None)


def _materialize(graph, question):
    from graph_rag import merge_sources
    from graph_retrieval import RetrievalIndex
    from integrated_context import enrich_sources
    from projection_cache import content_digest
    expression, short = expressions(question)
    selected = {}
    for kind in KINDS:
        if expression:
            rows = graph.workspace_store.rows('''SELECT s.id,s.body FROM search JOIN sources s ON s.id=search.rowid
                WHERE search MATCH ? AND s.kind=? ORDER BY bm25(search,3,1),s.id LIMIT 48''', (expression, kind))
            selected.update(rows)
        if short:
            clauses = ' OR '.join('(title LIKE ? OR text LIKE ?)' for _ in short)
            args = [v for word in short for v in ('%' + word + '%', '%' + word + '%')]
            selected.update(graph.workspace_store.rows(
                'SELECT id,body FROM sources WHERE kind=? AND (' + clauses + ') ORDER BY id LIMIT 16', [kind] + args))
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
    result.search_key = content_digest([graph.search_key, question, VERSION])
    result.prepared_index = RetrievalIndex(result)
    return result
