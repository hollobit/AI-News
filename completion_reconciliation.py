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

    def unchanged(self,cycle,row,stored,item):
        # Include final artifact bytes, workflow state/error and the entire current
        # item. A changed source, revoked verdict or policy can never hit this key.
        serialized=json.dumps([self.version,dict(stored) if stored else None,item],sort_keys=True,ensure_ascii=False)
        key=hashlib.sha256(serialized.encode()).hexdigest()
        previous=self.db.execute('SELECT input_hash FROM completion_admission_checks WHERE cycle_id=? AND document_id=?',
            (cycle,row['document_id'])).fetchone()
        same=bool(previous and previous[0]==key)
        self.hits+=int(same);self.misses+=int(not same)
        return same,key

    def accept(self,cycle,row,key):
        self.pending.append((cycle,row['document_id'],key))

    def flush(self):
        self.db.executemany('INSERT OR REPLACE INTO completion_admission_checks VALUES(?,?,?)',self.pending)
