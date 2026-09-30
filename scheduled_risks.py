"""Bounded, current-news-first deep analysis following fresh Telegram collection."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from corpus_completion import connect
from engine_errors import infrastructure_error
from recursive_improvement import owner_alive
from scheduled_collection import probe

ROOT = Path(__file__).resolve().parent
RECOVERABLE = {'timeout', 'queue_timeout', 'database_locked', 'network', 'capacity', 'circuit_open'}


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS risk_schedule (
        id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,
        launched_pid INTEGER,cycle_id TEXT,recoveries INTEGER NOT NULL DEFAULT 0,
        stagnant INTEGER NOT NULL DEFAULT 0,verified INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL DEFAULT 0)''')
    db.execute('INSERT OR IGNORE INTO risk_schedule(id) VALUES(1)')
    db.execute('''CREATE TABLE IF NOT EXISTS risk_schedule_events (
        seq INTEGER PRIMARY KEY,cycle_id TEXT,stage TEXT,detail TEXT,created_at TEXT)''')


def event(db, cycle, stage, detail):
    db.execute('INSERT INTO risk_schedule_events(cycle_id,stage,detail,created_at) VALUES(?,?,?,?)',
               (cycle,stage,json.dumps(detail,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))


def alive(pid):
    if not owner_alive(pid):return False
    process=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True)
    if process.returncode:return True  # Failed inspection is not proof of death.
    state=process.stdout.strip()
    return bool(state) and not state.startswith('Z')


def launch(path, cycle):
    command=[sys.executable,str(ROOT/'corpus_completion.py'),'--db',str(Path(path).resolve()),
             '--cycle',cycle,'--workers','1','--batch-size','1','--recent-first','--max-rounds','4']
    with (ROOT/'.runtime/scheduled-risks-worker.log').open('ab') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,start_new_session=True)
    return child.pid


def dispatch(path, *, enable=False, resume=False, launcher=launch, check_engine=probe, is_alive=alive, stamp=None, check_models=None):
    stamp=time.time() if stamp is None else stamp
    with connect(path) as db:
        init(db)
        schedule=dict(db.execute('SELECT * FROM risk_schedule WHERE id=1').fetchone())
        cycle=db.execute('SELECT * FROM rsi_cycles ORDER BY created_at DESC LIMIT 1').fetchone()
        if not cycle:return {'stage':'no_cycle'}
        cycle=dict(cycle);identity=cycle['id']
        if enable:
            db.execute('UPDATE risk_schedule SET enabled=1 WHERE id=1');schedule['enabled']=1
            event(db,identity,'enabled',{})
        if not schedule['enabled']:return {'stage':'disabled'}
        if is_alive(cycle['owner_pid']) or is_alive(schedule['launched_pid']):
            return {'stage':'running','cycle':identity}
        if resume:
            event(db,identity,'explicit_resume',{'prior_status':cycle['status'],'prior_error':cycle['error'],'prior_pause_requested':cycle['pause_requested']})
            db.execute('UPDATE rsi_cycles SET pause_requested=0 WHERE id=?',(identity,))
            cycle['pause_requested']=0
        if cycle['pause_requested']:return {'stage':'user_paused','cycle':identity}
        states=dict(db.execute("SELECT key,value FROM state WHERE key IN ('collector_last_success','collector_corpus_snapshot')"))
        heartbeat=states.get('collector_last_success')
        if not heartbeat or stamp-datetime.fromisoformat(heartbeat).timestamp()>120:return {'stage':'waiting_for_collector'}
        extraction=json.loads(states.get('collector_corpus_snapshot','{}'))
        if extraction.get('status')!='complete':return {'stage':'waiting_for_extraction'}
        db.commit()
        from model_access import ensure_model_access
        access=(check_models or ensure_model_access)()
        if not access['ready']:return {'stage':'model_access_required','model_access':access,'cycle':identity}
        verified=db.execute("SELECT COUNT(*) FROM corpus_completion_documents WHERE cycle_id=? AND status='complete'",(identity,)).fetchone()[0]
        if schedule['cycle_id']!=identity:
            schedule.update(recoveries=0,stagnant=0,verified=verified,next_attempt_at=0)
        if verified>schedule['verified']:schedule.update(stagnant=0,verified=verified)
        code=infrastructure_error(cycle['error']) if cycle['error'] else None
        if cycle['status'] in ('paused','error','failed') and not resume:
            if code not in RECOVERABLE:return {'stage':'attention_required','error_code':code,'cycle':identity}
            if schedule['recoveries']>=20 or schedule['stagnant']>=3:return {'stage':'recovery_limit','cycle':identity}
            if stamp<schedule['next_attempt_at']:return {'stage':'recovery_backoff','cycle':identity}
            schedule.update(recoveries=schedule['recoveries']+1,stagnant=schedule['stagnant']+1,
                            next_attempt_at=stamp+min(3600,300*2**schedule['stagnant']))
            db.execute('UPDATE risk_schedule SET cycle_id=?,recoveries=?,stagnant=?,verified=?,next_attempt_at=? WHERE id=1',
                       (identity,schedule['recoveries'],schedule['stagnant'],verified,schedule['next_attempt_at']))
            event(db,identity,'recovery_reserved',{'code':code,'attempt':schedule['recoveries']})
            db.commit()
            if not check_engine():return {'stage':'engine_unavailable','cycle':identity}
        elif resume:
            db.commit()
            if not check_engine():return {'stage':'engine_unavailable','cycle':identity}
        # A pause or new owner arriving during the probe always wins.
        current=db.execute('SELECT pause_requested,owner_pid FROM rsi_cycles WHERE id=?',(identity,)).fetchone()
        if current['pause_requested'] or is_alive(current['owner_pid']):return {'stage':'admission_changed'}
        if cycle['status']=='needs_review' and not resume:return {'stage':'review_required','cycle':identity}
        db.execute('UPDATE risk_schedule SET cycle_id=?,stagnant=?,verified=? WHERE id=1',(identity,schedule['stagnant'],verified))
        db.commit()
        pid=launcher(path,identity)
        db.execute('UPDATE risk_schedule SET launched_pid=? WHERE id=1',(pid,))
        event(db,identity,'launched',{'pid':pid,'workers':1,'batch_size':1,'max_rounds':4,'recent_first':True})
        return {'stage':'started','cycle':identity,'pid':pid,'verified_before':verified}


def main():
    from app import load_local_env
    load_local_env()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Explicit operator resume; preserves document/review attempts')
    args=parser.parse_args()
    with (ROOT/'.runtime/scheduled-risks.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"stage":"already_checking"}');return
        result=dispatch(ROOT/'data/news.sqlite3',enable=args.enable,resume=args.resume)
        result['checked_at']=datetime.now(timezone.utc).isoformat()
        (ROOT/'.runtime/scheduled-risks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
