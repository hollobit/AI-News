"""One cross-process recovery probe; original analysis checkpoints remain untouched."""
import os
import time
from engine_errors import EngineError, infrastructure_error

HARD_ERRORS = {'authentication', 'permission', 'configuration', 'rate_limit'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS llm_recovery (
        id INTEGER PRIMARY KEY CHECK(id=1), blocked INTEGER NOT NULL DEFAULT 0,
        failure_id INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        next_probe_at REAL NOT NULL DEFAULT 0, owner_pid INTEGER, lease_until REAL,
        success_at REAL NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '')''')
    db.execute('INSERT OR IGNORE INTO llm_recovery(id) VALUES(1)')
    db.execute('''CREATE TABLE IF NOT EXISTS llm_recovery_events (
        id INTEGER PRIMARY KEY, stage TEXT NOT NULL, created_at REAL NOT NULL,
        attempts INTEGER NOT NULL, owner_pid INTEGER, error_code TEXT NOT NULL)''')


def state(runtime):
    with runtime.db() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        return dict(db.execute('SELECT * FROM llm_recovery WHERE id=1').fetchone())


def synchronize(runtime):
    with runtime.db() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        saved = dict(db.execute('SELECT * FROM llm_recovery WHERE id=1').fetchone())
        if runtime._outage(db, time.time()):
            failures = db.execute("SELECT id,error_code FROM llm_calls WHERE status='failed' AND error_code NOT IN ('circuit_open','queue_timeout','owner_exited','owner_exited_waiting_lease') ORDER BY id DESC LIMIT 3").fetchall()
            last = failures[0] if failures else None
            code = next((r['error_code'] for r in failures if r['error_code'] in HARD_ERRORS),last['error_code'] if last else '')
            if last and last['id'] > saved['failure_id']:
                db.execute('UPDATE llm_recovery SET blocked=1,failure_id=?,error_code=? WHERE id=1', (last['id'],code))
        return dict(db.execute('SELECT * FROM llm_recovery WHERE id=1').fetchone())


def shared_probe(runtime, callback, *, automatic=True):
    """A lease plus cooldown coalesces scheduler and worker probes across processes."""
    stamp = time.time()
    with runtime.db() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        saved = dict(db.execute('SELECT * FROM llm_recovery WHERE id=1').fetchone())
        if saved['success_at'] > stamp - 15 and not saved['blocked']:
            return True
        from llm_runtime import _alive
        if saved['lease_until'] and (saved['lease_until'] > stamp or (saved['owner_pid'] and _alive(saved['owner_pid']))):
            return False
        if automatic and saved['error_code'] in HARD_ERRORS:
            return False
        if stamp < saved['next_probe_at'] or (automatic and saved['attempts'] >= 3):
            return False
        # Reserve before making the external call. A dead leader remains bounded by its lease.
        db.execute('UPDATE llm_recovery SET owner_pid=?,lease_until=?,attempts=attempts+1 WHERE id=1', (os.getpid(),stamp+45))
        db.execute('INSERT INTO llm_recovery_events(stage,created_at,attempts,owner_pid,error_code) VALUES(?,?,?,?,?)',
            ('automatic_probe' if automatic else 'operator_probe',stamp,saved['attempts']+1,os.getpid(),saved['error_code']))
    code = ''
    try:
        success = callback() is True
    except Exception as error:
        success = False
        code = infrastructure_error(str(error)) or 'execution_failed'
    with runtime.db() as db:
        db.execute('BEGIN IMMEDIATE')
        attempts = db.execute('SELECT attempts FROM llm_recovery WHERE id=1').fetchone()[0]
        db.execute('INSERT INTO llm_recovery_events(stage,created_at,attempts,owner_pid,error_code) VALUES(?,?,?,?,?)',
            ('probe_success' if success else 'probe_failed',time.time(),attempts,os.getpid(),code))
        if success:
            db.execute("UPDATE llm_recovery SET blocked=0,attempts=0,next_probe_at=0,owner_pid=NULL,lease_until=NULL,success_at=?,error_code='' WHERE id=1", (time.time(),))
        else:
            attempts = db.execute('SELECT attempts FROM llm_recovery WHERE id=1').fetchone()[0]
            db.execute('UPDATE llm_recovery SET blocked=1,owner_pid=NULL,lease_until=NULL,next_probe_at=?,error_code=CASE WHEN ? != ? THEN ? ELSE error_code END WHERE id=1',
                       (time.time()+min(300,30*2**(attempts-1)),code,'',code))
    return success


def wait_until_ready(runtime, callback, *, max_wait=180):
    deadline = time.monotonic() + max_wait
    while True:
        saved = synchronize(runtime)
        if not saved['blocked']:
            return
        if saved['error_code'] in HARD_ERRORS or saved['attempts'] >= 3:
            raise EngineError(saved['error_code'] if saved['error_code'] in HARD_ERRORS else 'circuit_open')
        if shared_probe(runtime, callback, automatic=True):
            return
        if time.monotonic() >= deadline:
            raise EngineError('circuit_open')
        time.sleep(min(1,max(0,deadline-time.monotonic())))


if __name__ == '__main__':
    import argparse
    import json
    from app import load_local_env
    from llm_runtime import LLMRuntime
    from scheduled_collection import _probe
    parser=argparse.ArgumentParser(description='공용 엔진 복구 상태와 명시적 단일 점검')
    parser.add_argument('--probe',action='store_true')
    args=parser.parse_args();load_local_env();runtime=LLMRuntime()
    synchronize(runtime)
    result={'success':shared_probe(runtime,_probe,automatic=False)} if args.probe else {}
    print(json.dumps(dict(result,recovery=state(runtime)),ensure_ascii=False))
