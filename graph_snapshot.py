"""Versioned graph and lexical postings, prepared off the HTTP request path.

Snapshots are JSON (never executable pickle), bound to the database revision and
correction ledger. An obsolete snapshot is not served while a replacement builds.
"""
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
import time
from pathlib import Path
import sqlite3
import threading

from evidence_search import LexicalIndex, VERSION
from evidence_corrections import correction_token
from projection_cache import revision_token, content_digest
from graph_rag import GraphResult, load_integrated_graph
from graph_retrieval import RetrievalIndex

FORMAT='graph-qa-snapshot-1:'+VERSION

def filters(params):
    result={}
    for key,value in params.items():
        if key in {'max_nodes','evidence_limit','view'}:continue
        value=value[0] if isinstance(value,(list,tuple)) and value else value
        if value and not (key=='date' and value=='all'):result[key]=[str(value)]
    return result

def revision(path,params):
    with sqlite3.connect(path,timeout=.25) as db:
        db.row_factory=sqlite3.Row
        token=revision_token(db)
        if token is None:raise RuntimeError('근거 저장소 갱신 중')
        return content_digest([FORMAT,token,correction_token(db),filters(params)])

def export_graph(graph):
    index=graph.prepared_index
    lexical=index.lexical
    return {'graph':dict(graph,nodes=list(index.nodes.values()),edges=index.edges,evidence=list(index.evidence.values())),
            'lengths':[[list(k),v] for k,v in lexical.lengths.items()],
            'postings':{term:[[list(k),v] for k,v in posting.items()] for term,posting in lexical.postings.items()},
            'average':lexical.average}

def restore_graph(payload,key):
    graph=GraphResult(payload['graph']);graph.search_key=key
    index=RetrievalIndex.__new__(RetrievalIndex)
    index.nodes={n['id']:n for n in graph.full_nodes};index.edges=graph.full_edges
    index.evidence={e['id']:e for e in graph.full_evidence}
    index.refs=defaultdict(set);index.adjacency=defaultdict(list)
    for n in index.nodes.values():
        for ref in n.get('evidence_ids',[]):
            if ref in index.evidence:index.refs[ref].add(n['id'])
    for e in index.edges:
        index.adjacency[e['source']].append(e);index.adjacency[e['target']].append(e)
    lexical=LexicalIndex.__new__(LexicalIndex)
    lexical.lengths={tuple(k):v for k,v in payload['lengths']}
    lexical.rows=lexical.lengths  # search only needs the number of indexed rows
    lexical.average=payload['average']
    lexical.postings={term:{tuple(k):v for k,v in rows} for term,rows in payload['postings'].items()}
    index.lexical=lexical;graph.prepared_index=index
    return graph

class GraphSnapshots:
    def __init__(self,path,directory=None,builder=None):
        self.path=str(Path(path).resolve())
        self.directory=Path(directory or (self.path+'.graph-snapshots'))
        self.directory.mkdir(parents=True,exist_ok=True)
        self.builder=builder or load_integrated_graph
        self.lock=threading.RLock();self.ready=OrderedDict();self.pending={};self.errors={}
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='graph-snapshots')

    def key(self,params):return revision(self.path,params)

    def request(self,params):
        params=filters(params);key=self.key(params)
        with self.lock:
            if key in self.ready:
                self.ready.move_to_end(key)
                return key,self.ready[key],None
            if key in self.errors and time.monotonic()-self.errors[key][1]<10:
                return key,None,None
            if key not in self.pending:
                if len(self.pending)>=4:return key,None,None
                future=self.executor.submit(self._prepare,key,params)
                self.pending[key]=future
                future.add_done_callback(lambda f:self._finished(key,f))
            return key,None,self.pending.get(key)

    def _finished(self,key,future):
        with self.lock:
            self.pending.pop(key,None)
            if not future.cancelled() and future.exception():
                self.errors[key]=(type(future.exception()).__name__,time.monotonic())
                while len(self.errors)>16:self.errors.pop(next(iter(self.errors)))

    def _prepare(self,key,params):
        file=self.directory/(content_digest(params)+'.json.gz')
        graph=None
        if file.exists():
            try:
                with gzip.open(file,'rt',encoding='utf-8') as handle:saved=json.load(handle)
                if saved.get('key')==key and saved.get('format')==FORMAT:
                    graph=restore_graph(saved['data'],key)
            except (OSError,EOFError,ValueError,KeyError,TypeError):pass
        if graph is None:
            with sqlite3.connect(self.path,timeout=15) as db:
                db.row_factory=sqlite3.Row
                graph=self.builder(db,params,for_retrieval=True)
            # Own a separate wrapper; shared projection caches must remain immutable.
            graph=GraphResult(dict(graph),full_nodes=graph.full_nodes,full_edges=graph.full_edges,full_evidence=graph.full_evidence)
            graph.search_key=key;graph.prepared_index=RetrievalIndex(graph)
            if self.key(params)!=key:raise RuntimeError('준비 중 근거 변경')
            temp=file.with_suffix(f'.{os.getpid()}.tmp')
            with gzip.open(temp,'wt',encoding='utf-8',compresslevel=1) as handle:
                json.dump({'format':FORMAT,'key':key,'data':export_graph(graph)},handle,ensure_ascii=False,separators=(',',':'))
            temp.replace(file)
            for old in sorted(self.directory.glob('*.json.gz'),key=lambda p:p.stat().st_mtime)[:-4]:old.unlink()
        if self.key(params)!=key:raise RuntimeError('저장 근거가 갱신되었습니다.')
        with self.lock:
            self.ready[key]=graph
            while len(self.ready)>3:self.ready.popitem(last=False)
        return graph

    def close(self):self.executor.shutdown(wait=False,cancel_futures=True)
