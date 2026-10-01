"""Cheap conservative publication preflight, never a substitute for review gates."""
import hashlib
import json
from pathlib import Path
import sqlite3
from code_policy import policy


def signature(db_path, root):
    if not Path(db_path).is_file(): return None
    with sqlite3.connect(db_path,timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS publication_revision(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO publication_revision VALUES(1,0)')
        tables=sorted(r[1] for r in db.execute('PRAGMA table_list') if r[2]=='table'
                      and not r[1].startswith(('sqlite_', 'publication_')) and r[1] not in {'evidence_blob_migrations'})
        triggers={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        changed=False
        for table in tables:
            for event in ('INSERT','UPDATE','DELETE'):
                name='publication_v1_'+hashlib.sha256((table+event).encode()).hexdigest()[:24]
                if name in triggers: continue
                quoted='"'+table.replace('"','""')+'"'
                db.execute(f'CREATE TRIGGER "{name}" AFTER {event} ON {quoted} BEGIN UPDATE publication_revision SET revision=revision+1 WHERE id=1; END')
                changed=True
        if changed:db.execute('UPDATE publication_revision SET revision=revision+1 WHERE id=1')
        version=db.execute('SELECT revision FROM publication_revision WHERE id=1').fetchone()[0]
        schema=db.execute('PRAGMA schema_version').fetchone()[0]
    inputs=[str(Path(db_path).resolve()),version,schema,policy('publication')]
    # Hash exact assets and saved observations without building views or graphs.
    for path in sorted((Path(root)/'static').glob('*')):
        if path.is_file():inputs.append((path.name,hashlib.sha256(path.read_bytes()).hexdigest()))
    for path in sorted(Path(str(db_path)+'.observatory').glob('*-v2.json')):
        inputs.append((path.name,hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()


def state_path(output):
    p=Path(output).absolute()
    return p.parent/(p.name+'.publication.json')
