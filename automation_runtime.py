"""Single server ownership and recovery of interrupted, previously active work."""
from contextlib import contextmanager
from pathlib import Path
import fcntl
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_LOCK=threading.RLock()
_STATE={'recovery':[],'views':{'status':'waiting','last_success':None,'last_error':None}}


def _alive(pid):
    if not pid:return False
    try:os.kill(int(pid),0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


@contextmanager
def server_lease(path):
    lock_path=Path(str(Path(path).resolve())+'.server.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('같은 뉴스 DB를 사용하는 웹 서버가 이미 실행 중입니다.') from error
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def scheduled_cycle(db, cycle_id):
    """A managed completion cycle belongs to its scheduler, never the web worker."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='risk_schedule'").fetchone():
        return False
    return bool(db.execute('SELECT 1 FROM risk_schedule WHERE enabled=1 AND cycle_id=?', (cycle_id,)).fetchone())


def interrupted_runs(path):
    """Capture before service constructors normalize abandoned running states."""
    result={}
    with sqlite3.connect(path) as db:
        db.row_factory=sqlite3.Row
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        specs={'baseline':('bulk_baseline_runs',"status IN ('preparing','running')"),
               'improvement':('rsi_cycles',"status IN ('running','waiting') AND pause_requested=0"),
               'workflows':('strategic_workflow_runs',"status IN ('queued','running')")}
        for kind,(table,where) in specs.items():
            if table not in tables:continue
            columns={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
            rows=db.execute('SELECT * FROM '+table+' WHERE '+where+' ORDER BY created_at DESC').fetchall()
            for row in rows:
                if kind == 'improvement' and scheduled_cycle(db, row['id']):
                    continue
                if kind == 'workflows' and 'request_json' in columns:
                    try:
                        workflow_owner = json.loads(row['request_json']).get('owner_pid')
                    except (ValueError, TypeError):
                        workflow_owner = None
                    if _alive(workflow_owner):
                        continue
                if 'owner_pid' not in columns or not _alive(row['owner_pid']):
                    result[kind]=row['id'];break
        # The recursive service owns resuming its child workflow checkpoint.
        if result.get('improvement'):
            result.pop('workflows',None)
        elif result.get('workflows') and 'rsi_rounds' in tables:
            if db.execute('SELECT 1 FROM rsi_rounds WHERE workflow_run_id=?',(result['workflows'],)).fetchone():
                result.pop('workflows',None)
    return result


def recover(candidates, services):
    """Resume each pre-existing run at most once; user-paused runs are not selected."""
    for kind,run_id in candidates.items():
        outcome={'kind':kind,'id':run_id}
        try:
            services[kind].resume(run_id)
            outcome['status']='resumed'
        except (ValueError,RuntimeError) as error:
            outcome.update(status='needs_attention',error=str(error)[:400])
        with _LOCK:_STATE['recovery'].append(outcome)


def view_status(status,error=None):
    with _LOCK:
        _STATE['views']['status']=status
        if status=='ready':
            _STATE['views']['last_success']=datetime.now(timezone.utc).isoformat()
            _STATE['views']['last_error']=None
        elif error:_STATE['views']['last_error']=str(error)[:400]


def runtime_status():
    from copy import deepcopy
    with _LOCK:return deepcopy(_STATE)
