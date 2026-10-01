"""Lossless content-addressed JSON storage; references never leave repository APIs."""
import hashlib
import json
import zlib

TAG = '$news_blob_v1'


def init(db):
    db.execute('CREATE TABLE IF NOT EXISTS evidence_blobs(hash TEXT PRIMARY KEY, body BLOB NOT NULL)')


def _save(db, value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    key=hashlib.sha256(raw).hexdigest()
    db.execute('INSERT OR IGNORE INTO evidence_blobs VALUES(?,?)',(key,zlib.compress(raw,3)))
    return {TAG:key}


def dumps(db, value):
    def pack(item):
        if isinstance(item,str) and len(item)>512: return _save(db,item)
        if isinstance(item,list): return [pack(v) for v in item]
        if isinstance(item,dict):
            if TAG in item: raise ValueError('Reserved blob key in input')
            return {k:pack(v) for k,v in item.items()}
        return item
    return json.dumps(_save(db,pack(value)),separators=(',',':'))


def loads(db, value):
    cache={}
    def unpack(item, ancestors=frozenset()):
        if isinstance(item,dict) and set(item)=={TAG}:
            key=item[TAG]
            if key in ancestors: raise ValueError('Cyclic evidence reference')
            if key not in cache:
                row=db.execute('SELECT body FROM evidence_blobs WHERE hash=?',(key,)).fetchone()
                if row is None: raise ValueError('Missing evidence blob')
                raw=zlib.decompress(row[0])
                if hashlib.sha256(raw).hexdigest()!=key: raise ValueError('Corrupt evidence blob')
                cache[key]=unpack(json.loads(raw),ancestors | {key})
            return cache[key]
        if isinstance(item,dict): return {k:unpack(v,ancestors) for k,v in item.items()}
        if isinstance(item,list): return [unpack(v,ancestors) for v in item]
        return item
    return unpack(json.loads(value))


def migrate_batch(db, table, column, limit=100):
    allowed={'improvement_catalog':('payload_json',),'improvement_catalog_history':('payload_json',),'improvement_ingestions':('result_json',),'bulk_baseline_documents':('snapshot_json','prepared_json','result_json')}
    if column not in allowed.get(table,()): raise ValueError('Unsupported evidence table')
    init(db)
    db.execute('CREATE TABLE IF NOT EXISTS evidence_blob_migrations(table_name TEXT PRIMARY KEY,last_rowid INTEGER NOT NULL)')
    checkpoint=table+':'+column
    previous=db.execute('SELECT last_rowid FROM evidence_blob_migrations WHERE table_name=?',(checkpoint,)).fetchone()
    start=previous[0] if previous else 0
    rows=db.execute(f'SELECT rowid,{column} FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?', (start,limit)).fetchall()
    before=after=converted=0
    for rowid,raw in rows:
        if raw is None: continue
        value=json.loads(raw)
        if not (isinstance(value,dict) and set(value)=={TAG}):
            packed=dumps(db,value)
            if loads(db,packed)!=value: raise ValueError('Evidence migration roundtrip failed')
            db.execute(f'UPDATE {table} SET {column}=? WHERE rowid=? AND {column}=?',(packed,rowid,raw))
            before+=len(raw.encode());after+=len(packed.encode());converted+=1
    if rows:
        db.execute('INSERT OR REPLACE INTO evidence_blob_migrations VALUES(?,?)',(checkpoint,rows[-1][0]))
    return dict(scanned=len(rows),converted=converted,column_bytes_before=before,column_bytes_after=after)
