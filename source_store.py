"""Versioned public source bodies, searchable passages and honest fetch health."""
import hashlib,json,re,sqlite3
from datetime import datetime,timezone
from link_groups import canonical_url
from evidence_search import terms,query_anchors,ALIASES


def now():return datetime.now(timezone.utc).isoformat()
def digest(text):return hashlib.sha256(text.encode()).hexdigest()

def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS source_versions(url TEXT,hash TEXT,text TEXT,title TEXT,scope TEXT,reader TEXT,published_at TEXT,fetched_at TEXT,truncated INTEGER,PRIMARY KEY(url,hash));
    CREATE TABLE IF NOT EXISTS source_health(url TEXT PRIMARY KEY,last_checked TEXT,last_success TEXT,last_status TEXT,error TEXT,error_kind TEXT,failures INTEGER DEFAULT 0,next_retry TEXT,hash TEXT,scope TEXT,reader TEXT);
    CREATE TABLE IF NOT EXISTS source_attempts(id INTEGER PRIMARY KEY,url TEXT,at TEXT,status TEXT,error_kind TEXT,error TEXT,reader TEXT);
    CREATE INDEX IF NOT EXISTS source_attempt_url ON source_attempts(url,id DESC);
    CREATE TABLE IF NOT EXISTS source_passages(id INTEGER PRIMARY KEY,url TEXT,hash TEXT,position INTEGER,start INTEGER,end INTEGER,text TEXT,title TEXT,published_at TEXT,scope TEXT,UNIQUE(url,position));
    CREATE VIRTUAL TABLE IF NOT EXISTS source_passages_fts USING fts5(text,title,content='source_passages',content_rowid='id',tokenize='unicode61');
    CREATE TRIGGER IF NOT EXISTS source_passage_insert AFTER INSERT ON source_passages BEGIN INSERT INTO source_passages_fts(rowid,text,title) VALUES(new.id,new.text,new.title); END;
    CREATE TRIGGER IF NOT EXISTS source_passage_delete AFTER DELETE ON source_passages BEGIN INSERT INTO source_passages_fts(source_passages_fts,rowid,text,title) VALUES('delete',old.id,old.text,old.title); END;
    CREATE TABLE IF NOT EXISTS source_reanalysis(url TEXT PRIMARY KEY,hash TEXT,status TEXT,run_id TEXT,error TEXT,updated_at TEXT);
    ''')


def error_kind(result):
    if result.get('status')=='fetched':return ''
    error=str(result.get('error','')).lower()
    if result.get('status')=='blocked':return 'blocked'
    if result.get('status')=='unsupported':return 'unsupported'
    for code,kind in [('429','rate_limit'),('404','not_found'),('403','access_denied')]:
        if code in error:return kind
    if '빈 본문' in error or 'empty' in error:return 'empty'
    if any(t in error for t in ('안내','접근 확인','captcha','challenge')):return 'challenge'
    return 'transient'


def chunks(text,size=1600):
    start=0;position=0
    while start<len(text):
        end=min(len(text),start+size)
        if end<len(text):
            split=text.rfind('\n',start+size//2,end)
            if split>start:end=split+1
        yield position,start,end,text[start:end]
        position+=1;start=end


def save(db,url,raw,stamp):
    """Caller owns transaction; failed refresh never destroys a good body."""
    from datetime import timedelta
    url=canonical_url(url);ok=raw.get('status')=='fetched';kind=error_kind(raw)
    old=db.execute('SELECT hash,failures,last_success FROM source_health WHERE url=?',(url,)).fetchone()
    prior_hash=old[0] if old else '';failures=0 if ok else (old[1] if old else 0)+1
    text=str(raw.get('text') or '')[:250000] if ok else '';hash_=digest(str(raw.get('title',''))+'\0'+str(raw.get('evidence_scope',''))+'\0'+text) if ok else prior_hash
    delay={'rate_limit':3600,'access_denied':21600,'challenge':21600,'empty':3600,'transient':900}.get(kind,7*86400)
    retry=(datetime.fromisoformat(stamp)+timedelta(seconds=min(7*86400,delay*2**min(failures-1,5)))).isoformat() if not ok else ''
    db.execute('INSERT INTO source_attempts(url,at,status,error_kind,error,reader) VALUES (?,?,?,?,?,?)',(url,stamp,raw.get('status','failed'),kind,str(raw.get('error',''))[:500],raw.get('reader','')))
    db.execute('DELETE FROM source_attempts WHERE url=? AND id NOT IN (SELECT id FROM source_attempts WHERE url=? ORDER BY id DESC LIMIT 12)',(url,url))
    db.execute('''INSERT INTO source_health VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET
    last_checked=excluded.last_checked,last_success=excluded.last_success,last_status=excluded.last_status,error=excluded.error,error_kind=excluded.error_kind,failures=excluded.failures,next_retry=excluded.next_retry,hash=excluded.hash,scope=CASE WHEN excluded.last_status='fetched' THEN excluded.scope ELSE source_health.scope END,reader=excluded.reader''',
      (url,stamp,stamp if ok else (old[2] if old else ''),raw.get('status','failed'),str(raw.get('error',''))[:500],kind,failures,retry,hash_,raw.get('evidence_scope',''),raw.get('reader','')))
    changed=ok and prior_hash!=hash_
    if ok:
        db.execute('INSERT OR IGNORE INTO source_versions VALUES (?,?,?,?,?,?,?,?,?)',(url,hash_,text,raw.get('title',''),raw.get('evidence_scope',''),raw.get('reader',''),raw.get('published_at',''),stamp,int(raw.get('truncated',False))))
        db.execute('DELETE FROM source_versions WHERE url=? AND hash NOT IN (SELECT hash FROM source_versions WHERE url=? ORDER BY fetched_at DESC LIMIT 3)',(url,url))
    if changed:
        db.execute('DELETE FROM source_passages WHERE url=?',(url,))
        db.executemany('INSERT INTO source_passages(url,hash,position,start,end,text,title,published_at,scope) VALUES (?,?,?,?,?,?,?,?,?)',
            [(url,hash_,p,a,b,t,raw.get('title',''),raw.get('published_at',''),raw.get('evidence_scope','')) for p,a,b,t in chunks(text)])
        db.execute("INSERT INTO source_reanalysis VALUES (?,?,'pending','','',?) ON CONFLICT(url) DO UPDATE SET hash=excluded.hash,status='pending',run_id='',error='',updated_at=excluded.updated_at",(url,hash_,stamp))
    return {'full_content_hash':hash_,'full_text_chars':len(text) if ok else 0,'full_text_truncated':bool(raw.get('truncated')), 'content_changed':changed,'next_retry':retry,'last_attempt_status':raw.get('status','failed'),'last_attempt_error':raw.get('error','')}


def search(db,question,limit=8,urls=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_passages'").fetchone():return []
    from morphology import PHRASES
    query=set(terms(question));words={w for w in query if not w.startswith('concept:')}
    for label,aliases in {**PHRASES,**ALIASES}.items():
        if 'concept:'+label.casefold() in query:words.update(aliases)
    words=sorted(words,key=lambda w:(-len(w),w))[:32]
    if not words:return []
    match=' OR '.join('"'+w.replace('"','""')+'"*' for w in words)
    args=[match];clause=''
    if urls is not None:
        urls=list(urls)[:100]
        if not urls:return []
        clause=' AND p.url IN ('+','.join('?' for _ in urls)+')';args+=urls
    rows=db.execute("SELECT p.* FROM source_passages_fts f JOIN source_passages p ON p.id=f.rowid WHERE source_passages_fts MATCH ? AND EXISTS (SELECT 1 FROM source_health h WHERE h.url=p.url AND h.hash=p.hash AND h.last_status='fetched')"+clause+' ORDER BY bm25(source_passages_fts) LIMIT 100',args)
    columns=[d[0] for d in rows.description];ranked=[];anchors=query_anchors(question)
    for values in rows:
        r=dict(zip(columns,values));tokens=set(terms(r['title']+' '+r['text']))
        if anchors and not anchors&tokens:continue
        r['score']=len(query&tokens);ranked.append(r)
    selected=[];counts={}
    for r in sorted(ranked,key=lambda r:-r['score']):
        if counts.get(r['url'],0)>=2:continue
        counts[r['url']]=counts.get(r['url'],0)+1
        selected.append(dict(id='passage:'+digest(r['url']+'\0'+r['hash']+'\0'+str(r['position']))[:24],
            document_id='url:'+r['url'],source_url=r['url'],title=r['title'],text=r['text'],day=str(r['published_at'])[:10] if re.match(r'^\d{4}-\d{2}-\d{2}',r['published_at'] or '') else '',
            source_kind='external',evidence_origin='public_source_passage',verification='unreviewed_source',evidence_scope=r['scope'],
            passage_index=r['position'],char_start=r['start'],char_end=r['end'],content_hash=r['hash']))
        if len(selected)>=limit:break
    return selected


def detail(db,url):
    url=canonical_url(url);db.row_factory=sqlite3.Row
    health=db.execute('SELECT * FROM source_health WHERE url=?',(url,)).fetchone()
    return dict(health=dict(health) if health else None,
      versions=[dict(r) for r in db.execute('SELECT hash,title,scope,reader,published_at,fetched_at,truncated,length(text) AS characters FROM source_versions WHERE url=? ORDER BY fetched_at DESC LIMIT 3',(url,))],
      attempts=[dict(r) for r in db.execute('SELECT * FROM source_attempts WHERE url=? ORDER BY id DESC LIMIT 12',(url,))])
