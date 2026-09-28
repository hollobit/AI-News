"""Revision-aware process-local projections; never caches network safety decisions."""
from collections import OrderedDict, Counter
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
import hashlib
import sqlite3
import threading
import uuid

_SOURCE = {'news','articles','archived_urls','source_excerpts'}
_ANALYSIS = {'bulk_baseline_cache','strategic_workflow_runs','strategic_workflow_artifacts',
             'research_documents','research_runs','graph_analysis','arxiv_papers',
             'arxiv_paper_mentions','arxiv_paper_analyses','keyword_index_meta'}
_LOCK = threading.RLock()
_CACHE = OrderedDict()
_FLIGHTS = {}
_SCHEMA = {}
_MAX_ENTRIES = 16384
_MAX_BYTES = 384 * 1024 * 1024
# Large independently loaded views cannot evict each other. Figures are bounded
# serialized-size estimates, not a promise about Python RSS. The compact news
# projection needs up to 256 MiB; the overall 384 MiB cap still applies.
_BUCKET_LIMITS = {name: mib*1024*1024 for name,mib in {
    'dataset':256,'graph':64,'graph_sources':96,'validated':64,'records':24,'strategy':48,'observatory':24,'other':8}.items()}
_BUCKET_BYTES = Counter()
_STATS = Counter()


def _bucket(namespace):
    if namespace == 'strategy-dataset-v1': return 'dataset'
    if namespace.startswith(('strategy-overview-','dynamic-strategy-')): return 'strategy'
    if namespace.startswith('observatory-'): return 'observatory'
    if namespace == 'integrated_graph': return 'graph'
    if namespace in {'graph_analysis_sources','baseline_current_snapshots'}: return 'graph_sources'
    if namespace in {'baseline_verified_content','paper_verified_content','workflow_verified_graph'}: return 'validated'
    if namespace == 'strategy-record-v1': return 'records'
    return 'other'


def cache_info():
    with _LOCK:
        return {'entries':len(_CACHE),'estimated_bytes':_CACHE_BYTES,'budget_bytes':_MAX_BYTES,
                'buckets':{name:{'estimated_bytes':_BUCKET_BYTES[name],'budget_bytes':limit} for name,limit in _BUCKET_LIMITS.items()},
                'counters':dict(_STATS),'inflight':len(_FLIGHTS)}


def _remove(key):
    global _CACHE_BYTES
    _CACHE.pop(key,None)
    size=_SIZES.pop(key,0)
    _CACHE_BYTES-=size
    _BUCKET_BYTES[_bucket(key[1])]-=size
_SIZES = {}
_CACHE_BYTES = 0


def _identity(db):
    path = next((row[2] for row in db.execute('PRAGMA database_list') if row[1]=='main'), '')
    if not path:
        return None
    path = Path(path).resolve()
    stat = path.stat()
    return str(path), stat.st_dev, stat.st_ino


def _setup(db, identity):
    schema = db.execute('PRAGMA schema_version').fetchone()[0]
    with _LOCK:
        if _SCHEMA.get(identity) == schema:
            return
    db.execute('CREATE TABLE IF NOT EXISTS projection_revisions(kind TEXT PRIMARY KEY,value INTEGER NOT NULL,identity TEXT NOT NULL)')
    uid = uuid.uuid4().hex
    for kind in ('source','analysis'):
        db.execute('INSERT OR IGNORE INTO projection_revisions VALUES (?,0,?)',(kind,uid))
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in sorted((_SOURCE | _ANALYSIS) & tables):
        kind = 'source' if table in _SOURCE else 'analysis'
        if table == 'source_excerpts':
            def meaning(prefix):
                value = f'{prefix}.result_json'
                safe = f"CASE WHEN json_valid({value}) THEN {value} ELSE '{{}}' END"
                return f"json_array(json_extract({safe},'$.status'),json_extract({safe},'$.title'),json_extract({safe},'$.text'))"
            db.execute('CREATE TABLE IF NOT EXISTS projection_source_meanings(url TEXT PRIMARY KEY,meaning TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO projection_source_meanings SELECT canonical_url,'+meaning('source_excerpts')+' FROM source_excerpts')
            for event in ('INSERT','UPDATE'):
                trigger = f'projection_v1_{table}_{event.lower()}'
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS "{trigger}" AFTER {event} ON source_excerpts
                    BEGIN
                    UPDATE projection_revisions SET value=value+1 WHERE kind='source' AND
                        COALESCE((SELECT meaning FROM projection_source_meanings WHERE url=NEW.canonical_url),'') IS NOT {meaning('NEW')};
                    INSERT INTO projection_source_meanings VALUES (NEW.canonical_url,{meaning('NEW')})
                        ON CONFLICT(url) DO UPDATE SET meaning=excluded.meaning;
                    END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS projection_v1_source_excerpts_delete AFTER DELETE ON source_excerpts
                BEGIN DELETE FROM projection_source_meanings WHERE url=OLD.canonical_url;
                UPDATE projection_revisions SET value=value+1 WHERE kind='source'; END''')
            continue
        columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        for event in ('INSERT','UPDATE','DELETE'):
            condition = ''
            if table == 'strategic_workflow_artifacts' and 'stage' in columns:
                condition = " WHEN (" + ("OLD.stage='final' OR NEW.stage='final'" if event=='UPDATE' else
                                         ("OLD.stage='final'" if event=='DELETE' else "NEW.stage='final'"))
                condition += ')'
                if 'strategic_workflow_runs' in tables:
                    prefix='OLD' if event=='DELETE' else 'NEW'
                    condition += f" AND EXISTS (SELECT 1 FROM strategic_workflow_runs WHERE id={prefix}.run_id AND status='complete')"
            elif table == 'strategic_workflow_runs' and {'status','error'} <= columns:
                condition = ' WHEN ' + ("(OLD.status='complete' OR NEW.status='complete') AND (OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error)" if event=='UPDATE'
                    else "NEW.status='complete'" if event=='INSERT' else "OLD.status='complete'")
            trigger = f'projection_v1_{table}_{event.lower()}'
            if table in {'strategic_workflow_runs','strategic_workflow_artifacts'}:
                db.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
                trigger = f'projection_v2_{table}_{event.lower()}'
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS "{trigger}" AFTER {event} ON "{table}"{condition}
                BEGIN UPDATE projection_revisions SET value=value+1 WHERE kind='{kind}'; END''')
    db.commit()
    schema = db.execute('PRAGMA schema_version').fetchone()[0]
    with _LOCK:
        _SCHEMA[identity] = schema


def revision_token(db, kinds=('source','analysis')):
    """Install change triggers once; transactions/memory/read-only safely bypass caching."""
    if db.in_transaction:
        return None
    identity = _identity(db)
    if identity is None:
        return None
    if any(kind not in {'source','analysis'} for kind in kinds):
        raise ValueError('Unknown projection revision kind')
    try:
        _setup(db, identity)
    except sqlite3.OperationalError as exc:
        if db.in_transaction:
            db.rollback()
        if 'readonly' in str(exc).lower() or 'locked' in str(exc).lower():
            return None
        raise
    values = {row[0]:(row[1],row[2]) for row in db.execute('SELECT kind,value,identity FROM projection_revisions')}
    return identity, tuple((kind,*values[kind]) for kind in kinds), db.execute('PRAGMA schema_version').fetchone()[0]


def content_digest(value):
    import json
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def cached_read(db, namespace, revision, builder, *, copy_result=True):
    """Return an isolated copy; a shared Future prevents duplicate concurrent builds."""
    global _CACHE_BYTES
    clone = deepcopy if copy_result else lambda value: value
    if revision is None:
        return builder()
    identity = _identity(db)
    if identity is None or db.in_transaction:
        return builder()
    key = (identity,namespace,revision)
    with _LOCK:
        hit = key in _CACHE
        if hit:
            _CACHE.move_to_end(key)
            value = _CACHE[key]
            _STATS[namespace+':hit'] += 1
        else:
            owner = key not in _FLIGHTS
            _STATS[namespace+(':miss' if owner else ':wait')] += 1
            future = _FLIGHTS.setdefault(key,Future())
    if hit:
        return clone(value)
    if not owner:
        return clone(future.result())
    try:
        value = builder()
        stored = deepcopy(value)
        import pickle
        size = len(pickle.dumps(stored,protocol=5)) * 3
        with _LOCK:
            bucket = _bucket(namespace)
            limit = min(_MAX_BYTES,_BUCKET_LIMITS[bucket])
            if size <= limit:
                # The dataset/snapshot/source list needs only its newest revision.
                if namespace in {'strategy-dataset-v1','baseline_current_snapshots','graph_analysis_sources','strategy-overview-v1'}:
                    for old in list(_CACHE):
                        if old[:2] == key[:2]: _remove(old)
                if key in _CACHE: _remove(key)
                _CACHE[key] = stored
                _SIZES[key] = size
                _CACHE_BYTES += size
                _BUCKET_BYTES[bucket] += size
                while _BUCKET_BYTES[bucket] > limit:
                    removed = next(old for old in _CACHE if _bucket(old[1])==bucket)
                    _STATS[removed[1]+':eviction'] += 1
                    _remove(removed)
                while len(_CACHE)>_MAX_ENTRIES or _CACHE_BYTES>_MAX_BYTES:
                    removed=next(iter(_CACHE))
                    _STATS[removed[1]+':eviction'] += 1
                    _remove(removed)
            else:
                _STATS[namespace+':oversized'] += 1
        future.set_result(stored)
        return value
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _LOCK:
            _FLIGHTS.pop(key,None)
