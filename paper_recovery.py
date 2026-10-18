"""Bounded infrastructure recovery, separate from semantic review attempts."""
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import time

from engine_errors import infrastructure_error

RECOVERABLE = {'circuit_open','timeout','queue_timeout','network','capacity','execution_failed','database_locked'}
MAX_RECOVERIES = 3


def setup(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS paper_analysis_recovery(
        paper_id TEXT,input_hash TEXT,attempts INTEGER NOT NULL DEFAULT 0,
        next_at REAL NOT NULL DEFAULT 0,error_code TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(paper_id,input_hash));
        CREATE TABLE IF NOT EXISTS paper_analysis_events(
        id INTEGER PRIMARY KEY,paper_id TEXT,input_hash TEXT,stage TEXT,error_code TEXT,
        attempt INTEGER,created_at REAL NOT NULL);''')


def engine_ready():
    """Read the shared circuit; never probe, clear it, or route around limits."""
    from llm_runtime import DEFAULT_PATH, LLMRuntime
    path=Path(os.environ.get('NEWS_LLM_RUNTIME_DB') or DEFAULT_PATH).resolve()
    if not path.exists():return True  # First execution initializes normal admission.
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
            db.row_factory=sqlite3.Row
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'llm_recovery' in tables:
                row=db.execute('SELECT blocked FROM llm_recovery WHERE id=1').fetchone()
                if row and row[0]:return False
            return 'llm_calls' not in tables or not LLMRuntime._outage(db,time.time())
    except sqlite3.Error:return False


def eligible(db, row, stamp):
    code=infrastructure_error(row.get('error'))
    if row['status']!='failed' or code not in RECOVERABLE:return False
    recovery=db.execute('SELECT attempts,next_at FROM paper_analysis_recovery WHERE paper_id=? AND input_hash=?',
                        (row['paper_id'],row['input_hash'])).fetchone()
    if recovery:return recovery[0]<MAX_RECOVERIES and recovery[1]<=stamp
    try:failed_at=datetime.fromisoformat(row.get('updated_at') or '').timestamp()
    except ValueError:failed_at=stamp
    return failed_at+60<=stamp


def reserve(db, row, stamp):
    """Reserve before enqueue, preserving old counters and the original error."""
    if not eligible(db,row,stamp):return False
    code=infrastructure_error(row.get('error'))
    db.execute('''INSERT INTO paper_analysis_recovery VALUES(?,?,1,?,?)
        ON CONFLICT(paper_id,input_hash) DO UPDATE SET attempts=attempts+1,next_at=excluded.next_at,error_code=excluded.error_code''',
        (row['paper_id'],row['input_hash'],stamp+60,code))
    attempt=db.execute('SELECT attempts FROM paper_analysis_recovery WHERE paper_id=? AND input_hash=?',
                       (row['paper_id'],row['input_hash'])).fetchone()[0]
    db.execute('INSERT INTO paper_analysis_events(paper_id,input_hash,stage,error_code,attempt,created_at) VALUES(?,?,?,?,?,?)',
               (row['paper_id'],row['input_hash'],'infrastructure_retry_reserved',code,attempt,stamp))
    return True


def failed(db, row, error):
    code=infrastructure_error(str(error)) or 'content_or_format'
    previous=db.execute('SELECT attempts FROM paper_analysis_recovery WHERE paper_id=? AND input_hash=?',
                        (row['paper_id'],row['input_hash'])).fetchone()
    attempt=previous[0] if previous else 0
    stamp=time.time()
    db.execute('INSERT INTO paper_analysis_events(paper_id,input_hash,stage,error_code,attempt,created_at) VALUES(?,?,?,?,?,?)',
               (row['paper_id'],row['input_hash'],'execution_failed',code,attempt,stamp))
    db.execute('''INSERT INTO paper_analysis_recovery VALUES(?,?,?,?,?)
        ON CONFLICT(paper_id,input_hash) DO UPDATE SET next_at=excluded.next_at,error_code=excluded.error_code''',
        (row['paper_id'],row['input_hash'],attempt,stamp+min(3600,60*2**attempt),code))


def summary(db, stamp=None):
    """Read-only diagnostics; eligibility does not mean an engine is available."""
    stamp=time.time() if stamp is None else stamp
    result=dict(retry_ready=0,retry_waiting=0,retry_exhausted=0,attention_required=0)
    columns={r[1] for r in db.execute('PRAGMA table_info(arxiv_paper_analyses)')}
    if not {'paper_id','input_hash','error','updated_at'}<=columns:return result
    has_recovery=db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_analysis_recovery'").fetchone()
    recoveries={(r[0],r[1]):(r[2],r[3]) for r in db.execute('SELECT paper_id,input_hash,attempts,next_at FROM paper_analysis_recovery')} if has_recovery else {}
    for identity,signature,error,updated in db.execute("SELECT paper_id,input_hash,error,updated_at FROM arxiv_paper_analyses WHERE status='failed'"):
        if infrastructure_error(error) not in RECOVERABLE:
            result['attention_required']+=1;continue
        attempts,next_at=recoveries.get((identity,signature),(0,None))
        if next_at is None:
            try:next_at=datetime.fromisoformat(updated or '').timestamp()+60
            except ValueError:next_at=stamp+60
        key='retry_exhausted' if attempts>=MAX_RECOVERIES else 'retry_waiting' if next_at>stamp else 'retry_ready'
        result[key]+=1
    return result
