"""Incremental message/dedup/corpus projections over a transactional change feed.

This database is disposable. Source edits and review/run history stay in the
archive. An incomplete/stale projection is never returned to an HTTP reader.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from task_lifecycle import checkpoint, cancellable_db
from source_changes import position
from verified_cache import policy


def encode(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def message_key(chat,message):return encode([str(chat),int(message)])


def _schema(store):
    store.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY,epoch TEXT,seq INTEGER,policy TEXT,metrics TEXT);
        CREATE TABLE IF NOT EXISTS messages(key TEXT PRIMARY KEY,signature TEXT,published TEXT,message_id INTEGER,active INTEGER,source_order INTEGER);
        CREATE INDEX IF NOT EXISTS messages_signature ON messages(signature);
        CREATE TABLE IF NOT EXISTS rows(message TEXT,section INTEGER,position INTEGER,day TEXT,published TEXT,message_id INTEGER,item_index INTEGER,payload TEXT,PRIMARY KEY(message,section,position));
        CREATE TABLE IF NOT EXISTS urls(url TEXT,message TEXT,PRIMARY KEY(url,message));
        CREATE TABLE IF NOT EXISTS contributions(message TEXT,section INTEGER,position INTEGER,identity TEXT,context TEXT,day TEXT,published TEXT,payload TEXT,PRIMARY KEY(message,section,position,identity));
        CREATE INDEX IF NOT EXISTS contribution_identity ON contributions(identity);
        CREATE TABLE IF NOT EXISTS documents(identity TEXT PRIMARY KEY,score REAL,day TEXT,published TEXT,payload TEXT,source_rows INTEGER);
    ''')


def _ready(store, pos):
    try:row=store.execute('SELECT epoch,seq,policy FROM meta WHERE id=1').fetchone()
    except sqlite3.OperationalError:return False
    return bool(row and row==(pos[0],pos[1],policy()))


@contextmanager
def snapshot(db):
    from projection_cache import is_read_projection
    pos=position(db)
    if pos is None:
        yield None
        return
    filename=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'), '')
    if not filename:
        yield None
        return
    path=Path(filename+'.source-projections.sqlite3')
    readonly=is_read_projection() or db.in_transaction
    if readonly and not path.exists():
        yield None
        return
    store=None
    try:
        store=sqlite3.connect(path.resolve().as_uri()+'?mode=ro' if readonly else str(path),uri=readonly,timeout=2)
        cancellable_db(store)
        if not readonly:_schema(store)
        if not _ready(store,pos):
            if readonly:
                store.close();store=None
            else:
                db.execute('BEGIN')
                try:
                    pos=position(db)
                    store.execute('BEGIN IMMEDIATE')
                    if not _ready(store,pos):_refresh(db,store,pos)
                    store.commit()
                except BaseException:
                    store.rollback()
                    raise
                finally:db.rollback()
        if store:store.execute('BEGIN')
        # A second writer may have committed after we selected pos. Pin both
        # the metadata and result rows in the same sidecar read transaction.
        if store and not _ready(store,pos):store.close();store=None
    except sqlite3.Error:
        if store:store.close();store=None
    except BaseException:
        if store:store.close()
        raise
    try:yield store
    finally:
        if store:store.close()


def _message(db, store, key):
    from news_repository import joined_articles, unindexed_link_rows, hidden_link_rows, normalized_message, article_key
    from improvement_selection import content_identity
    from link_groups import canonical_url,extract_links
    from url_context import focus_url_context
    chat,mid=json.loads(key)
    raw=db.execute('SELECT rowid,* FROM news WHERE chat_id=? AND message_id=?',(chat,mid)).fetchone()
    hidden=list(db.execute("SELECT original_url,title FROM archived_urls WHERE chat_id=? AND message_id=? AND active=1 AND entity_type='text_link'",(chat,mid)))
    signature=hashlib.sha256(encode([chat,normalized_message(raw['text']),sorted(set(tuple(r) for r in hidden))]).encode()).hexdigest() if raw else 'orphan:'+key
    source_order=raw['rowid'] if raw else 0
    published=raw['published_at'] if raw else ''
    store.execute('INSERT INTO messages VALUES(?,?,?,?,1,?)',(key,signature,published,mid,source_order))
    args={'_message':(chat,mid),'_duplicates':set()}
    joined=joined_articles(db,**args)
    extra=unindexed_link_rows(db,joined,**args)
    hidden_rows=hidden_link_rows(db,joined+extra,**args)
    for section,rows in enumerate((joined,extra,hidden_rows)):
        for index,row in enumerate(rows):
            checkpoint()
            if section==0:row=dict(row,_repository_key=article_key(row))
            store.execute('INSERT INTO rows VALUES(?,?,?,?,?,?,?,?)',(key,section,index,row.get('day',''),row.get('published_at',''),mid,row.get('item_index',0),encode(row)))
            urls=list(dict.fromkeys(canonical_url(url) for url in [row.get('source_url') or '',*extract_links(row.get('text') or '')] if canonical_url(url)))
            for url in urls or ['']:
                item=focus_url_context(row,url) if url else dict(row,source_url=url)
                item.pop('_repository_key',None)
                identity=content_identity(item)
                item['id']=identity.removeprefix('news:') if not url else 'corpus_'+hashlib.sha256(url.encode()).hexdigest()[:24]
                context=hashlib.sha256(json.dumps([item.get('title'),item.get('text')],ensure_ascii=False).encode()).hexdigest()
                store.execute('INSERT INTO contributions VALUES(?,?,?,?,?,?,?,?)',(key,section,index,identity,context,str(item.get('day') or ''),str(item.get('published_at') or ''),encode(item)))
                if url:store.execute('INSERT OR IGNORE INTO urls VALUES(?,?)',(url,key))
    return signature


def _refresh(db,store,pos):
    from source_enrichment import attach_sources
    from strategic_value import evaluate_news
    previous=store.execute('SELECT epoch,seq,policy FROM meta WHERE id=1').fetchone()
    full=not previous or previous[0]!=pos[0] or previous[1]<pos[2] or previous[2]!=policy()
    reason='initial' if not previous else 'epoch' if previous[0]!=pos[0] else 'history_gap' if previous[1]<pos[2] else 'policy' if previous[2]!=policy() else 'delta'
    changed=set();affected=set();signatures=set()
    if full:
        for table in ('messages','rows','urls','contributions','documents'):store.execute('DELETE FROM '+table)
        for table in ('news','articles','archived_urls'):
            changed.update(message_key(*r) for r in db.execute(f'SELECT DISTINCT chat_id,message_id FROM {table}'))
    else:
        for kind,raw in db.execute('SELECT kind,key_json FROM source_changes WHERE seq>? AND seq<=?',(previous[1],pos[1])):
            checkpoint()
            key=json.loads(raw)
            if kind=='message':changed.add(message_key(*key))
            elif kind=='url':changed.update(r[0] for r in store.execute('SELECT message FROM urls WHERE url=?',(key[0],)))
    # Resolve both the old and new duplicate groups. An unchanged alternative
    # message may become the representative when the first copy is removed.
    def related(signature):
        return [r[0] for r in store.execute('SELECT key FROM messages WHERE signature=?',(signature,))]
    def affect(keys):
        for key in keys:
            affected.update(r[0] for r in store.execute('SELECT identity FROM contributions WHERE message=?',(key,)))
    for key in sorted(changed):
        checkpoint()
        old=store.execute('SELECT signature FROM messages WHERE key=?',(key,)).fetchone()
        if old:
            signatures.add(old[0]);affect(related(old[0]))
        for table,column in (('rows','message'),('urls','message'),('contributions','message'),('messages','key')):
            store.execute(f'DELETE FROM {table} WHERE {column}=?',(key,))
        signature=_message(db,store,key)
        signatures.add(signature);affect(related(signature))
    for signature in signatures:
        members=list(store.execute('SELECT key FROM messages WHERE signature=? ORDER BY published,message_id,key',(signature,)))
        for index,(key,) in enumerate(members):
            store.execute('UPDATE messages SET active=? WHERE key=?',(int(index==0),key))
    # Primary latest-date choice is the old algorithm's choice. Remaining ties
    # use its original joined -> unparsed -> hidden source traversal order.
    for identity in affected:
        checkpoint()
        candidates=list(store.execute('''SELECT c.payload,c.context FROM contributions c JOIN messages m ON m.key=c.message
            WHERE c.identity=? AND m.active=1 ORDER BY c.day DESC,c.published DESC,c.section,
            CASE WHEN c.section=0 THEN -m.message_id END,
            CASE WHEN c.section=0 THEN c.position END,
            CASE WHEN c.section=1 THEN m.source_order END,
            CASE WHEN c.section=2 THEN m.published END,
            CASE WHEN c.section=2 THEN m.message_id END,c.position,c.message''',(identity,)))
        if not candidates:
            store.execute('DELETE FROM documents WHERE identity=?',(identity,));continue
        item=attach_sources(db,[json.loads(candidates[0][0])])[0]
        item.update(corpus_identity=identity,source_item_index=item.get('item_index'),
            item_index=-int(hashlib.sha256(identity.encode()).hexdigest()[:15],16)-1,
            distinct_contexts=len({r[1] for r in candidates}),
            selection_reason='전체 텔레그램 뉴스 대기열 · 정규 URL/동일 본문 중복 제거',strategic_value=evaluate_news(item))
        store.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)',(identity,item['strategic_value']['score'],str(item.get('day') or ''),str(item.get('published_at') or ''),encode(item),len(candidates)))
    metrics={'mode':reason,'changed_messages':len(changed),'changed_documents':len(affected),'checkpoint':pos[1]}
    store.execute('INSERT OR REPLACE INTO meta VALUES(1,?,?,?,?)',(pos[0],pos[1],policy(),encode(metrics)))


def joined(db):
    with snapshot(db) as store:
        if store is None:return None
        result=[json.loads(r[0]) for r in store.execute('''SELECT r.payload FROM rows r JOIN messages m ON m.key=r.message
            WHERE r.section=0 AND m.active=1 ORDER BY r.day DESC,r.published DESC,r.message_id DESC,r.item_index,r.message''')]
    from reach_pipeline import external_rows
    from source_enrichment import attach_sources
    return result+attach_sources(db,external_rows(db))


def corpus(db, source_urls=None):
    from improvement_selection import SelectionBatch
    with snapshot(db) as store:
        if store is None:return None
        rows=store.execute('SELECT payload,source_rows FROM documents ORDER BY score DESC,day DESC,published DESC,identity DESC')
        items=[];count=0
        for body,total in rows:
            checkpoint()
            item=json.loads(body)
            if source_urls is not None and item.get('source_url','') not in source_urls:continue
            items.append(item);count+=total
        coverage={'total_unique':len(items),'source_rows':count,'duplicates_excluded':count-len(items),
            'scope':'all_telegram_news','unit':'정규 URL 또는 URL 없는 동일 제목·본문',
            'text_scope':'대표 뉴스 문맥과 조회 가능한 원문 발췌; 반복 게시 문맥은 원문 보관함에 보존'}
        return SelectionBatch(items,coverage)
