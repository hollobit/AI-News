"""Bounded JSON spill files for views too large for the in-memory cache."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import threading
from task_lifecycle import checkpoint
from verified_cache import policy

NAMESPACES = {'integrated_graph','graph_analysis_sources','risk-page-v1'}
MAX_DISK_BYTES = 512 * 1024 * 1024


def location(db, namespace, revision):
    raw = next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'), '')
    if not raw: return None
    identity = hashlib.sha256(json.dumps([namespace,revision,policy()],sort_keys=True,default=str).encode()).hexdigest()
    return Path(raw+'.view-snapshots') / (identity+'.json.gz')


def read(db, namespace, revision):
    file = location(db,namespace,revision)
    if not file or not file.exists(): return None,False
    try:
        with gzip.open(file,'rt',encoding='utf-8') as handle:
            payload=json.load(handle)
        checkpoint()
        if payload['kind']=='graph':
            from graph_rag import GraphResult
            value=GraphResult(payload['value'],**payload['full'])
        elif payload['kind']=='tuple': value=tuple(payload['value'])
        else: value=payload['value']
        return value,True
    except (OSError,EOFError,ValueError,KeyError,TypeError):
        return None,False


def write(db, namespace, revision, value):
    from projection_cache import is_read_projection
    if is_read_projection(): return
    file=location(db,namespace,revision)
    if not file: return
    file.parent.mkdir(exist_ok=True)
    full = {key:getattr(value,key) for key in ('full_nodes','full_edges','full_evidence') if hasattr(value,key)}
    payload={'kind':'graph' if full else 'tuple' if isinstance(value,tuple) else 'json','value':value,'full':full}
    temporary=file.with_suffix(f'.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        checkpoint()
        with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=1) as handle:
            json.dump(payload,handle,ensure_ascii=False,separators=(',',':'))
        checkpoint()
        temporary.replace(file)
        total=0
        for index,old in enumerate(sorted(file.parent.glob('*.json.gz'),key=lambda p:p.stat().st_mtime,reverse=True)):
            total += old.stat().st_size
            if index>=8 or total>MAX_DISK_BYTES:
                old.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
