"""SQLite-backed retrieval with the same admitted records and BM25 scores.

Only matching postings and selected graph records are hydrated by HTTP workers.
The immutable database is produced in a separate preparation process.
"""
import json
import os
import uuid
import weakref
import sqlite3
from contextlib import closing
from collections import defaultdict
from collections.abc import Mapping
from functools import lru_cache, cached_property
from math import log1p
from pathlib import Path
from task_lifecycle import checkpoint
from evidence_search import terms


def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


class Store:
    def __init__(self,path):
        self.path=str(Path(path).resolve())
        lease=Path(self.path+'.lease-'+str(os.getpid())+'-'+uuid.uuid4().hex)
        lease.touch()
        self.release=weakref.finalize(self,lease.unlink,missing_ok=True)
    def rows(self,sql,args=()):
        with closing(sqlite3.connect(Path(self.path).as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            return db.execute(sql,args).fetchall()


class Records(Mapping):
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    def __init__(self,store,kind):self.store=store;self.kind=kind
    @lru_cache(maxsize=128)
    def __getitem__(self,key):
        rows=self.store.rows('SELECT body FROM records WHERE kind=? AND id=?',(self.kind,key))
        if not rows:raise KeyError(key)
        return json.loads(rows[0][0])
    def __iter__(self):return iter(r[0] for r in self.store.rows('SELECT id FROM records WHERE kind=? ORDER BY position',(self.kind,)))
    def __len__(self):return self.store.rows('SELECT count(*) FROM records WHERE kind=?',(self.kind,))[0][0]
    @cached_property
    def identities(self):return frozenset(r[0] for r in self.store.rows('SELECT id FROM records WHERE kind=?',(self.kind,)))
    def __contains__(self,key):return key in self.identities


class Relations:
    def __init__(self,store,kind):self.store=store;self.kind=kind
    @lru_cache(maxsize=128)
    def __getitem__(self,key):
        if self.kind=='refs':return {r[0] for r in self.store.rows('SELECT node FROM refs WHERE evidence=?',(key,))}
        return [json.loads(r[0]) for r in self.store.rows('SELECT body FROM edges WHERE source=? OR target=? ORDER BY position',(key,key))]


class Edges:
    def __init__(self,store):self.store=store
    def between(self,ids):
        ids=sorted(ids)
        if not ids:return []
        marks=','.join('?' for _ in ids)
        return [json.loads(r[0]) for r in self.store.rows(f'SELECT body FROM edges WHERE source IN ({marks}) AND target IN ({marks}) ORDER BY position',ids+ids)]


class Postings(Mapping):
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    def __init__(self,store):self.store=store
    @lru_cache(maxsize=24)
    def __getitem__(self,term):
        rows=self.store.rows('SELECT kind,id,frequency FROM postings WHERE term=?',(term,))
        if not rows:raise KeyError(term)
        return {(kind,key):frequency for kind,key,frequency in rows}
    def __iter__(self):return iter(r[0] for r in self.store.rows('SELECT DISTINCT term FROM postings'))
    def __len__(self):return self.store.rows('SELECT count(DISTINCT term) FROM postings')[0][0]


class DiskLexical:
    def __init__(self,store,count,average):self.store=store;self.count=count;self.average=average;self.postings=Postings(store)
    def search(self,question):
        scores=defaultdict(float)
        for term in set(terms(question)):
            checkpoint()
            rows=self.store.rows('SELECT p.kind,p.id,p.frequency,r.length FROM postings p JOIN records r ON r.kind=p.kind AND r.id=p.id WHERE p.term=?',(term,))
            weight=log1p((self.count-len(rows)+.5)/(len(rows)+.5))
            for kind,key,frequency,length in rows:
                normalizer=1.2*(.25+.75*length/self.average)
                scores[(kind,key)]+=weight*frequency*2.2/(frequency+normalizer)
        return dict(scores)


def write(path,graph,key):
    from graph_retrieval import RetrievalIndex
    index=RetrievalIndex(graph)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE meta(key TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE records(kind TEXT,id TEXT,position INTEGER,body TEXT,length INTEGER,PRIMARY KEY(kind,id));
            CREATE TABLE postings(term TEXT,kind TEXT,id TEXT,frequency REAL,PRIMARY KEY(term,kind,id)) WITHOUT ROWID;
            CREATE TABLE refs(evidence TEXT,node TEXT,PRIMARY KEY(evidence,node)) WITHOUT ROWID;
            CREATE TABLE edges(position INTEGER PRIMARY KEY,source TEXT,target TEXT,body TEXT);
            CREATE INDEX edge_source ON edges(source);CREATE INDEX edge_target ON edges(target);''')
        db.execute('INSERT INTO meta VALUES(?,?)',('key',key))
        db.execute('INSERT INTO meta VALUES(?,?)',('graph',encode({k:v for k,v in graph.items() if k not in {'nodes','edges','evidence'}})))
        db.execute('INSERT INTO meta VALUES(?,?)',('lexical',encode([len(index.lexical.rows),index.lexical.average])))
        for kind,records in (('n',index.nodes),('e',index.evidence)):
            db.executemany('INSERT INTO records VALUES(?,?,?,?,?)',((kind,key,i,encode(value),index.lexical.lengths[(kind,key)]) for i,(key,value) in enumerate(records.items())))
        for term,posting in index.lexical.postings.items():
            checkpoint()
            db.executemany('INSERT INTO postings VALUES(?,?,?,?)',((term,kind,key,value) for (kind,key),value in posting.items()))
        db.executemany('INSERT INTO refs VALUES(?,?)',((ref,node) for ref,nodes in index.refs.items() for node in nodes))
        db.executemany('INSERT INTO edges VALUES(?,?,?,?)',((i,e['source'],e['target'],encode(e)) for i,e in enumerate(index.edges)))


def read(path,key):
    from graph_rag import GraphResult
    from graph_retrieval import RetrievalIndex
    store=Store(path)
    if store.rows("SELECT body FROM meta WHERE key='key'")[0][0]!=key:raise ValueError('Obsolete graph index')
    graph=GraphResult(json.loads(store.rows("SELECT body FROM meta WHERE key='graph'")[0][0]))
    index=RetrievalIndex.__new__(RetrievalIndex)
    index.nodes=Records(store,'n');index.evidence=Records(store,'e');index.edges=Edges(store)
    index.refs=Relations(store,'refs');index.adjacency=Relations(store,'adjacency')
    index.lexical=DiskLexical(store,*json.loads(store.rows("SELECT body FROM meta WHERE key='lexical'")[0][0]))
    graph.search_key=key;graph.prepared_index=index
    return graph


def preview(graph,params):
    """Hydrate only the presentation cap, retaining the full disk index for search."""
    from copy import deepcopy
    from graph_rag import NODE_LIMIT,EDGE_LIMIT
    def limit(name,default,maximum):
        try:return max(1,min(maximum,int(params.get(name,[str(default)])[0])))
        except (ValueError,TypeError):return default
    size=limit('max_nodes',NODE_LIMIT,NODE_LIMIT);evidence_limit=limit('evidence_limit',1000000,1000000)
    index=graph.prepared_index;store=index.nodes.store
    result=deepcopy(dict(graph))
    nodes=[json.loads(r[0]) for r in store.rows("SELECT body FROM records WHERE kind='n' ORDER BY position LIMIT ?",(size,))]
    edges=index.edges.between({n['id'] for n in nodes})[:EDGE_LIMIT]
    refs={ref for row in nodes+edges for ref in row['evidence_ids']};evidence=[]
    ids=sorted(refs)
    for start in range(0,len(ids),400):
        batch=ids[start:start+400]
        evidence.extend(store.rows("SELECT position,body FROM records WHERE kind='e' AND id IN ("+','.join('?' for _ in batch)+')',batch))
    result.update(nodes=nodes,edges=edges,evidence=[json.loads(body) for _,body in sorted(evidence)[:evidence_limit]])
    result.setdefault('limits',{}).update(nodes=size,evidence=evidence_limit)
    totals=result.get('totals',{})
    result['truncated']=len(nodes)<totals.get('nodes',len(nodes)) or len(edges)<totals.get('edges',len(edges)) or len(result['evidence'])<len(refs)
    return result


def prune(directory,keep=4,budget=1024*1024*1024):
    """Evict disposable generations only when no live reader holds a lease."""
    total=count=0
    for file in sorted(Path(directory).glob('*.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True):
        protected=False
        for lease in file.parent.glob(file.name+'.lease-*'):
            try:
                pid=int(lease.name.split('.lease-',1)[1].split('-',1)[0]);os.kill(pid,0)
                protected=True
            except ProcessLookupError:lease.unlink(missing_ok=True)
            except (ValueError,PermissionError):protected=True
        if protected:continue
        count+=1;total+=file.stat().st_size
        if count>keep or total>budget:file.unlink(missing_ok=True)
