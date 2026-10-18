"""Workflow publication journal with the same boundaries as projection revisions."""
import uuid


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS workflow_search_state(id INTEGER PRIMARY KEY,epoch TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO workflow_search_state VALUES(1,?)',(uuid.uuid4().hex,))
    db.execute('CREATE TABLE IF NOT EXISTS workflow_search_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,old_id TEXT,new_id TEXT)')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    triggers={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    added=False
    for table in ('strategic_workflow_runs','strategic_workflow_artifacts'):
        if table not in tables:continue
        columns={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        column='id' if table.endswith('_runs') else 'run_id'
        if column not in columns:continue
        for event in ('INSERT','UPDATE','DELETE'):
            condition=''
            if table.endswith('_runs') and {'status','error'}<=columns:
                condition=' WHEN '+("(OLD.status='complete' OR NEW.status='complete') AND (OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error)" if event=='UPDATE'
                    else "NEW.status='complete'" if event=='INSERT' else "OLD.status='complete'")
            elif table.endswith('_artifacts') and 'stage' in columns:
                condition=" WHEN ("+("OLD.stage='final' OR NEW.stage='final'" if event=='UPDATE' else "OLD.stage='final'" if event=='DELETE' else "NEW.stage='final'")+")"
                if 'strategic_workflow_runs' in tables:
                    prefix='OLD' if event=='DELETE' else 'NEW'
                    condition+=f" AND EXISTS(SELECT 1 FROM strategic_workflow_runs WHERE id={prefix}.run_id AND status='complete')"
            old='NULL' if event=='INSERT' else f'OLD.{column}'
            new='NULL' if event=='DELETE' else f'NEW.{column}'
            name=f'workflow_search_v1_{table}_{event.lower()}'
            added|=name not in triggers
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON {table}{condition} BEGIN
                INSERT INTO workflow_search_changes(old_id,new_id) VALUES({old},{new});
                DELETE FROM workflow_search_changes WHERE seq<=(SELECT MAX(seq)-10000 FROM workflow_search_changes);
                END''')
    if added:db.execute('UPDATE workflow_search_state SET epoch=? WHERE id=1',(uuid.uuid4().hex,))


def position(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_search_state'").fetchone():return None
    return dict(epoch=db.execute('SELECT epoch FROM workflow_search_state WHERE id=1').fetchone()[0],
                seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM workflow_search_changes').fetchone()[0])


def changes(db,previous,current):
    if not previous or not current or previous['epoch']!=current['epoch']:return None
    delta=current['seq']-previous['seq']
    if not 0<=delta<=256:return None
    rows=db.execute('SELECT seq,old_id,new_id FROM workflow_search_changes WHERE seq>? AND seq<=? ORDER BY seq',
                    (previous['seq'],current['seq'])).fetchall()
    if len(rows)!=delta:return None
    return delta,{key for row in rows for key in row[1:] if key}
