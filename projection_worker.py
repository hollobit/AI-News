"""Disposable CPU preparation outside the HTTP interpreter; no model calls."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from task_lifecycle import checkpoint


def prepare(*args):
    process=subprocess.Popen([sys.executable,__file__,*map(str,args)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            checkpoint();time.sleep(.1)
        if process.returncode:raise RuntimeError('Projection worker failed')
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    mode,path,*args=sys.argv[1:]
    with sqlite3.connect(path,timeout=15) as db:
        db.row_factory=sqlite3.Row
        if mode=='strategy':
            from strategy_views import dataset
            from document_features import persist
            persist(db,dataset(db)['items'])
        elif mode=='observatory':
            from observatory import read_observatory
            window=int(args[0]);expanded=args[1]=='1';file=Path(args[2])
            result=read_observatory(db,window,expanded)
            temporary=file.with_suffix('.worker.tmp')
            temporary.write_text(json.dumps({'window':window,'expanded':expanded,'data':result},ensure_ascii=False))
            temporary.replace(file)
        else:raise SystemExit('Unknown projection mode')
