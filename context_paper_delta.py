"""Exact paper publication keys, synchronized with analysis revision triggers."""
import uuid

TABLES=('arxiv_papers','arxiv_paper_mentions','arxiv_paper_analyses')
LIMIT=10000


def publication_condition(table,columns):
    """Checkpoint/progress fields never affect current reviewed paper content."""
    fields=('paper_id','input_hash','status','error','result_json')
    if table!='arxiv_paper_analyses' or not set(fields)<=set(columns):return ''
    return ' WHEN '+' OR '.join(f'OLD.{key} IS NOT NEW.{key}' for key in fields)


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS paper_search_state(id INTEGER PRIMARY KEY,epoch TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO paper_search_state VALUES(1,?)',(uuid.uuid4().hex,))
    db.execute('CREATE TABLE IF NOT EXISTS paper_search_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,old_id TEXT,new_id TEXT)')
    triggers={r[0]:r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
    added=False
    for table in TABLES:
        columns={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if 'paper_id' not in columns:continue
        for event in ('INSERT','UPDATE','DELETE'):
            name=f'paper_search_v1_{table}_{event.lower()}'
            condition=publication_condition(table,columns) if event=='UPDATE' else ''
            if condition and name in triggers and 'paper-public-v2' not in triggers[name]:
                db.execute(f'DROP TRIGGER {name}');triggers.pop(name)
            added|=name not in triggers
            old='NULL' if event=='INSERT' else 'OLD.paper_id'
            new='NULL' if event=='DELETE' else 'NEW.paper_id'
            marker='/* paper-public-v2 */' if condition else ''
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON {table}{condition} BEGIN
                {marker}
                INSERT INTO paper_search_changes(old_id,new_id) VALUES({old},{new});
                DELETE FROM paper_search_changes WHERE seq<=(SELECT MAX(seq)-{LIMIT} FROM paper_search_changes);
                END''')
    if added:db.execute('UPDATE paper_search_state SET epoch=? WHERE id=1',(uuid.uuid4().hex,))


def position(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_search_state'").fetchone():return None
    return dict(epoch=db.execute('SELECT epoch FROM paper_search_state WHERE id=1').fetchone()[0],
                seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM paper_search_changes').fetchone()[0])


def changes(db,previous,current):
    if not previous or not current or previous['epoch']!=current['epoch']:return None
    delta=current['seq']-previous['seq']
    if not 0<=delta<=256:return None
    rows=db.execute('SELECT seq,old_id,new_id FROM paper_search_changes WHERE seq>? AND seq<=? ORDER BY seq',
                    (previous['seq'],current['seq'])).fetchall()
    if len(rows)!=delta:return None
    return delta,{key for row in rows for key in row[1:] if key}
