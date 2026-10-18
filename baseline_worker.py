"""Detached baseline worker; HTTP shutdown never terminates this process."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import threading
from bulk_baseline import BulkBaselineService
from database import open_db
from improvement_selection import all_corpus_items


def run(path, action, run_id, settings, ready):
    lock_path=Path(str(Path(path).resolve())+'.baseline-worker.lock')
    with lock_path.open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def selector():
            with open_db(path) as db:return all_corpus_items(db)
        service=BulkBaselineService(path,selector)
        with service.db() as db:
            initial_seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM bulk_baseline_events').fetchone()[0]
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda *_:service.stop.set())
        if action=='handoff':
            token=settings.get('extracted_at')
            if not isinstance(token,str) or not token:raise ValueError('Missing handoff token')
            result=service.resume(run_id,handoff_token=token)
        else:
            result=service.start(settings) if action=='start' else service.resume(run_id,settings=settings)
        ready.write_text(json.dumps({'run':result}))
        done=threading.Event()
        def watch_pause():
            while not done.wait(.2):
                with service.db() as db:
                    stopped=db.execute("SELECT 1 FROM bulk_baseline_events WHERE run_id=? AND stage='user_pause_requested' AND seq>? LIMIT 1",(result['id'],initial_seq)).fetchone()
                if stopped:
                    service.stop.set()
                    return
        watcher=threading.Thread(target=watch_pause,daemon=True);watcher.start()
        try:
            if service.thread:service.thread.join()
        finally:
            done.set();watcher.join(1);service.close()
        with service.db() as db:
            final=db.execute("SELECT status,error FROM bulk_baseline_runs WHERE id=?",(result['id'],)).fetchone()
            handoff=final['error']=='new_input_handoff' or final['status'] in ('complete','requires_review')
    if handoff:
        from pipeline_wakeup import notify
        notify(path,risks=False)


if __name__=='__main__':
    from app import load_local_env
    load_local_env()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--action',choices=['start','resume','handoff'],required=True)
    parser.add_argument('--run-id',default='')
    parser.add_argument('--settings',default='{}')
    parser.add_argument('--ready',required=True)
    args=parser.parse_args();ready=Path(args.ready)
    try:run(args.db,args.action,args.run_id,json.loads(args.settings),ready)
    except Exception as error:
        # Never expose CLI/provider inputs or authentication details in IPC.
        ready.write_text(json.dumps({'error_type':type(error).__name__}))
        raise SystemExit(1)
