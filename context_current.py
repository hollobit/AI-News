"""Validate a question's selected source records independently of unrelated writes."""
import json
import sqlite3
import threading
from pathlib import Path
from collections import defaultdict,OrderedDict
from contextlib import closing

from projection_cache import content_digest

_CORPUS=OrderedDict()
_LOCK=threading.RLock()


def corpus(db, *, input_hashes=None):
    """Reuse only a committed source generation inside our read transaction."""
    from improvement_selection import all_corpus_items
    from verified_cache import policy
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    filename=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'),'')
    if not filename or db.total_changes or not db.in_transaction or not {'news','articles','projection_revisions'}<=tables:
        return all_corpus_items(db)
    row=db.execute("SELECT value,identity FROM projection_revisions WHERE kind='source'").fetchone()
    if not row:return all_corpus_items(db)
    path=Path(filename).resolve();stat=path.stat()
    key=(str(path),stat.st_dev,stat.st_ino,tuple(row),db.execute('PRAGMA schema_version').fetchone()[0],policy())
    with _LOCK:
        if key not in _CORPUS:_CORPUS[key]={'items':all_corpus_items(db),'inputs':None}
        _CORPUS.move_to_end(key)
        while len(_CORPUS)>2:_CORPUS.popitem(last=False)
        entry=_CORPUS[key]
        if input_hashes is None:return entry['items']
        if entry['inputs'] is None:
            from bulk_baseline import freeze_item
            from task_lifecycle import checkpoint
            inputs={}
            for item in entry['items']:
                checkpoint()
                inputs[freeze_item(item)['input_hash']]=item
            # Publish only a complete lookup. Cancellation cannot leave a partial
            # generation, and values share the original items rather than copies.
            entry['inputs']=inputs
        return [entry['inputs'][key] for key in sorted(input_hashes) if key in entry['inputs']]


def identity(source):
    return json.dumps([source['kind'],source['id'],source.get('row',{}).get('run_id')],ensure_ascii=False,separators=(',',':'))


def normalized(source,final=None):
    source=dict(source);result=dict(source['result'])
    for field in ('nodes','edges','evidence'):
        if field in result:result[field]=sorted(result[field],key=lambda item:
            (str(item.get('id') or ''),json.dumps(item,ensure_ascii=False,sort_keys=True)))
    source['result']=result
    if final is not None:
        source['workspace_final']={k:final[k] for k in ('risk_report','risk_verification','risk_verified','evidence') if k in final}
    return source


def load(db,keys):
    """Same publication gates as the index, applied only to selected records."""
    from graph_rag import _analysis_sources
    from integrated_context import enrich_sources
    selected=defaultdict(set)
    for key in keys:
        kind,record,_=json.loads(key);selected[kind].add(record)
    sources,_=_analysis_sources(db,selected=selected,
        baseline_items=corpus(db,input_hashes=selected['baseline']) if selected.get('baseline') else [])
    if selected.get('wiki'):sources+=enrich_sources(db,[])
    current=[]
    wanted=set(keys)
    for source in sources:
        if identity(source) not in wanted:continue
        final=None
        if source['kind']=='workflow':
            row=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(source['id'],)).fetchone()
            if row:final=json.loads(row[0])
        current.append(normalized(source,final))
    return current


def dependencies(sources):
    return {identity(s):content_digest(normalized(s)) for s in sources}


def current(path,expected,passages=()):
    """No model calls. Current input, review, withdrawals and full passages count."""
    if not isinstance(expected,dict) or not expected:return False
    with closing(sqlite3.connect(path,timeout=2)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        actual=dependencies(load(db,expected))
        if actual!=expected:return False
        for p in passages:
            row=db.execute("SELECT hash,last_status FROM source_health WHERE url=?",(p['url'],)).fetchone()
            if not row or row[0]!=p['hash'] or row[1]!='fetched':return False
    return True
