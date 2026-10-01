"""Reuse only successful admission checks bound to exact input and validator code.

Writes are buffered until validation finishes, so CPU validation holds no writer
lock. Missing cache entries or any changed input always take the original path.
"""
import hashlib
import json
from verified_cache import policy


class AdmissionChecks:
    def __init__(self,db):
        self.db=db
        db.execute('''CREATE TABLE IF NOT EXISTS completion_admission_checks(
            cycle_id TEXT,document_id TEXT,input_hash TEXT,PRIMARY KEY(cycle_id,document_id))''')
        db.commit()
        self.version=policy()
        self.pending=[]
        self.hits=0
        self.misses=0
        self.known={}

    def unchanged(self,cycle,row,stored,item):
        # Include final artifact bytes, workflow state/error and the entire current
        # item. A changed source, revoked verdict or policy can never hit this key.
        serialized=json.dumps([self.version,dict(stored) if stored else None,item],sort_keys=True,ensure_ascii=False)
        key=hashlib.sha256(serialized.encode()).hexdigest()
        if cycle not in self.known:
            self.known[cycle]=dict(self.db.execute('SELECT document_id,input_hash FROM completion_admission_checks WHERE cycle_id=?',(cycle,)))
        same=self.known[cycle].get(row['document_id'])==key
        self.hits+=int(same);self.misses+=int(not same)
        return same,key

    def accept(self,cycle,row,key):
        self.pending.append((cycle,row['document_id'],key))
        self.known.setdefault(cycle,{})[row['document_id']]=key

    def flush(self):
        self.db.executemany('INSERT OR REPLACE INTO completion_admission_checks VALUES(?,?,?)',self.pending)


def report_summaries(db, cycle):
    """Digest immutable report bytes once, invalidating on every relevant write."""
    db.execute('CREATE TABLE IF NOT EXISTS completion_report_digests(run_id TEXT PRIMARY KEY,digest TEXT NOT NULL)')
    for table in ('strategic_workflow_runs','strategic_workflow_artifacts'):
        column='id' if table.endswith('_runs') else 'run_id'
        for event in ('INSERT','UPDATE','DELETE'):
            refs=('OLD','NEW') if event=='UPDATE' else ('OLD',) if event=='DELETE' else ('NEW',)
            body=' '.join(f'DELETE FROM completion_report_digests WHERE run_id={ref}.{column};' for ref in refs)
            condition='' if column=='id' else " WHEN "+' OR '.join(f"{ref}.stage='final'" for ref in refs)
            if column=='id' and event=='UPDATE': condition=' WHEN OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error'
            db.execute(f'CREATE TRIGGER IF NOT EXISTS completion_digest_{table}_{event} AFTER {event} ON {table}{condition} BEGIN {body} END')
    db.commit()
    rows=db.execute("""SELECT DISTINCT r.id,r.status,r.error,h.digest FROM corpus_completion_documents d
        JOIN strategic_workflow_runs r ON r.id=d.workflow_run_id
        LEFT JOIN completion_report_digests h ON h.run_id=r.id
        WHERE d.cycle_id=? AND d.status='complete'""",(cycle,)).fetchall()
    summaries={};pending=[]
    for row in rows:
        value=dict(row)
        if value['digest'] is None:
            body=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(value['id'],)).fetchone()
            value['digest']=hashlib.sha256((body[0] if body else '').encode()).hexdigest()
            # Store only if report bytes still match this read; concurrent edits
            # must never restore a digest deleted by an invalidation trigger.
            pending.append((value['id'],value['digest'],body[0] if body else None))
        summaries[value['id']]=value
    for run,digest,body in pending:
        db.execute("""INSERT OR REPLACE INTO completion_report_digests SELECT ?,?
            WHERE (SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final') IS ?""",(run,digest,run,body))
    db.commit()
    return summaries
