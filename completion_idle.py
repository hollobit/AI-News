"""Skip a completed cycle only while its validated inputs remain unchanged."""
import json


def token(db):
    from projection_cache import revision_token
    from context_workflow_delta import position
    from evidence_corrections import correction_token
    from verified_cache import policy
    source=revision_token(db,('source',))
    workflow=position(db)
    if source is None or workflow is None:return None
    return json.dumps(dict(source=source,workflow=workflow,corrections=correction_token(db),
                           policy=policy(),schema=db.execute('PRAGMA schema_version').fetchone()[0]),sort_keys=True)


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS completion_idle_checks(cycle_id TEXT PRIMARY KEY,input_token TEXT NOT NULL)')


def remember(db,cycle,before):
    if before and token(db)==before:
        db.execute('INSERT OR REPLACE INTO completion_idle_checks VALUES(?,?)',(cycle,before))


def unchanged(db,cycle):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='completion_idle_checks'").fetchone():return False
    row=db.execute('SELECT input_token FROM completion_idle_checks WHERE cycle_id=?',(cycle,)).fetchone()
    return bool(row and row[0]==token(db))
