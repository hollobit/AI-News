"""Transactional source-key changes with a bounded replay window."""
import json
import uuid

LIMIT = 10000


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS source_change_state(id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL,floor INTEGER NOT NULL DEFAULT 0)')
    db.execute('INSERT OR IGNORE INTO source_change_state(id,epoch) VALUES(1,?)',(uuid.uuid4().hex,))
    db.execute('CREATE TABLE IF NOT EXISTS source_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,key_json TEXT NOT NULL)')
    existing_triggers={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    new_tracking=False
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table,kind,columns in [('news','message',('chat_id','message_id')),('articles','message',('chat_id','message_id')),
            ('archived_urls','message',('chat_id','message_id')),('source_excerpts','url',('canonical_url',)),
            ('reach_observations','external',('url',))]:
        if table not in tables:continue
        actual={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if not set(columns)<=actual:continue
        if len(columns)==2:
            db.execute(f'CREATE INDEX IF NOT EXISTS source_change_{table}_message ON {table}(chat_id,message_id)')
        for event in ('INSERT','UPDATE','DELETE'):
            new_tracking |= f'source_change_v1_{table}_{event.lower()}' not in existing_triggers
            statements=[]
            for prefix in (('OLD','NEW') if event=='UPDATE' else ('OLD',) if event=='DELETE' else ('NEW',)):
                values=','.join(f'{prefix}.{c}' for c in columns)
                statements.append(f"INSERT INTO source_changes(kind,key_json) VALUES('{kind}',json_array({values}));")
            statements.append(f'UPDATE source_change_state SET floor=MAX(floor,(SELECT MAX(seq)-{LIMIT} FROM source_changes)) WHERE id=1;')
            statements.append('DELETE FROM source_changes WHERE seq<=(SELECT floor FROM source_change_state WHERE id=1);')
            db.execute(f'CREATE TRIGGER IF NOT EXISTS source_change_v1_{table}_{event.lower()} AFTER {event} ON {table} BEGIN '+''.join(statements)+' END')

    if new_tracking:
        db.execute('UPDATE source_change_state SET epoch=? WHERE id=1',(uuid.uuid4().hex,))


def position(db):
    if db is None:return None
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_change_state'").fetchone():return None
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    triggers={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    for table in {'news','articles','archived_urls','source_excerpts','reach_observations'} & tables:
        if any(f'source_change_v1_{table}_{event}' not in triggers for event in ('insert','update','delete')):
            return None
    row=db.execute('SELECT epoch,floor FROM source_change_state WHERE id=1').fetchone()
    latest=db.execute("SELECT seq FROM sqlite_sequence WHERE name='source_changes'").fetchone()
    return row[0],latest[0] if latest else 0,row[1]
