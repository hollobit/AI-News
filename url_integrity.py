"""Versioned repair of derived links from preserved Telegram snapshots only."""
import json
from datetime import datetime,timezone
from link_groups import canonical_url
from url_parser import links

VERSION='lossless-links-v2'


def repair(db, *, apply=False):
    from url_archive import extract_message_urls,_store_url_occurrences
    changes=[];archives=[]
    for row in db.execute("SELECT * FROM articles WHERE source_url!=''"):
        old=row['source_url']
        candidates=[u for u in links(row['text']) if u!=old and u.startswith(old) and u[len(old):len(old)+1] in ')]}']
        if len(set(candidates))==1:changes.append((dict(row),candidates[0]))
    for row in db.execute('SELECT * FROM message_snapshots'):
        expected=extract_message_urls(json.loads(row['payload_json']))
        current=db.execute('SELECT original_url,canonical_url FROM archived_urls WHERE snapshot_id=? ORDER BY occurrence',(row['snapshot_id'],)).fetchall()
        wanted=[(v['original_url'],canonical_url(v['original_url'])) for v in expected]
        if [tuple(r) for r in current]!=wanted:archives.append(dict(row))
    result={'version':VERSION,'article_urls':len(changes),'archive_snapshots':len(archives),'applied':apply}
    if not apply:return result
    db.execute('SAVEPOINT repair_url_integrity')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS url_integrity_history(
            seq INTEGER PRIMARY KEY,kind TEXT,identity TEXT,prior_json TEXT,repaired_at TEXT)''')
        db.execute('CREATE TABLE IF NOT EXISTS repaired_source_links(old_url TEXT,new_url TEXT,PRIMARY KEY(old_url,new_url))')
        stamp=datetime.now(timezone.utc).isoformat()
        for old,url in changes:
            db.execute('INSERT OR IGNORE INTO repaired_source_links VALUES(?,?)',(old['source_url'],url))
            identity=json.dumps([old['chat_id'],old['message_id'],old['item_index']])
            db.execute('INSERT INTO url_integrity_history(kind,identity,prior_json,repaired_at) VALUES(?,?,?,?)',('article',identity,json.dumps(old,ensure_ascii=False),stamp))
            db.execute('UPDATE articles SET source_url=? WHERE chat_id=? AND message_id=? AND item_index=?',(url,old['chat_id'],old['message_id'],old['item_index']))
        for snapshot in archives:
            prior=[dict(r) for r in db.execute('SELECT * FROM archived_urls WHERE snapshot_id=? ORDER BY occurrence',(snapshot['snapshot_id'],))]
            db.execute('INSERT INTO url_integrity_history(kind,identity,prior_json,repaired_at) VALUES(?,?,?,?)',('archive',snapshot['snapshot_id'],json.dumps(prior,ensure_ascii=False),stamp))
            db.execute('DELETE FROM archived_urls WHERE snapshot_id=?',(snapshot['snapshot_id'],))
            _store_url_occurrences(db,snapshot,json.loads(snapshot['payload_json']))
        db.execute('INSERT OR REPLACE INTO url_archive_meta VALUES(?,?)',(VERSION,json.dumps(result)))
        db.execute('RELEASE SAVEPOINT repair_url_integrity')
    except Exception:
        db.execute('ROLLBACK TO SAVEPOINT repair_url_integrity');db.execute('RELEASE SAVEPOINT repair_url_integrity');raise
    return result


def ensure(db):
    if not db.execute('SELECT 1 FROM url_archive_meta WHERE key=?',(VERSION,)).fetchone():
        return repair(db,apply=True)
