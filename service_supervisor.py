"""Bounded restart supervision; adopt existing processes and honor analysis pauses."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def matching_processes(service, output):
    result=[]
    for line in output.splitlines():
        parts=line.strip().split(None,1)
        if len(parts)!=2 or not parts[0].isdigit():
            continue
        try: args=shlex.split(parts[1])
        except ValueError: continue
        if len(args)<2 or not Path(args[0]).name.startswith('python'):
            continue
        script=Path(args[1])
        if script.is_absolute() and script.parent!=ROOT:
            continue
        if service in ('server','collector'):
            command='serve' if service=='server' else 'collect'
            matches=script.name=='app.py' and len(args)>2 and args[2]==command
        else:
            matches=script.name=='corpus_completion.py'
        if matches: result.append(int(parts[0]))
    return result


def deep_command(db):
    row=db.execute('SELECT id,status,pause_requested,owner_pid FROM rsi_cycles ORDER BY created_at DESC LIMIT 1').fetchone()
    if not row or row['pause_requested'] or row['status'] not in ('running','waiting','finishing'):
        return None
    if row['owner_pid']:
        try: os.kill(row['owner_pid'],0);return None
        except ProcessLookupError: pass
        except PermissionError: return None
    return [sys.executable,str(ROOT/'corpus_completion.py'),'--cycle',row['id'],
            '--workers','1','--batch-size','1','--review-first']


def run(service):
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    with (ROOT/'.runtime'/('supervisor-'+service+'.lock')).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        child=None;failures=0;started=0;next_start=0
        while not stopping:
            if child is not None and child.poll() is not None:
                failures=failures+1 if time.monotonic()-started<300 else 0
                next_start=time.monotonic()+min(900,30*2**min(failures,5))
                print(json.dumps(dict(service=service,event='exited',code=child.returncode,failures=failures)),flush=True)
                child=None
            output=subprocess.run(['ps','-axo','pid=,command='],capture_output=True,text=True,check=True).stdout
            if child is None and not matching_processes(service,output) and time.monotonic()>=next_start:
                if service=='deep':
                    with sqlite3.connect((ROOT/'data/news.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                        db.row_factory=sqlite3.Row;command=deep_command(db)
                else:
                    command=[sys.executable,str(ROOT/'app.py'),'serve','--port','8001'] if service=='server' else [sys.executable,str(ROOT/'app.py'),'collect']
                if command and failures<3:
                    child=subprocess.Popen(command,cwd=ROOT);started=time.monotonic()
                    print(json.dumps(dict(service=service,event='started',pid=child.pid)),flush=True)
            for _ in range(30):
                if stopping:break
                time.sleep(1)
        # Adopted processes are never signalled. Managed children checkpoint normally.
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT)
            child.wait()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service',choices=['server','collector','deep'])
    run(parser.parse_args().service)
