"""Start deep admission before baseline admission for one collection wakeup."""
import argparse
from pathlib import Path
import subprocess
import sys
import threading


def notify(path, *, risks=True):
    root=Path(__file__).resolve().parent
    # Production schedulers are bound to this DB. Tests/isolated DBs cannot
    # accidentally enqueue work against the production corpus.
    if Path(path).resolve()!=root/'data/news.sqlite3':return
    # This detached coordinator survives a collector exit. Waiting happens there,
    # never on the message ingestion/HTTP thread. Deep dispatch commits its live
    # worker PID before baseline tests whether a source coalescing window is safe.
    command=[sys.executable,str(root/'pipeline_wakeup.py')]
    if risks:command.append('--risks')
    process=subprocess.Popen(command,cwd=root,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,start_new_session=True)
    threading.Thread(target=process.wait,daemon=True,name='pipeline-wakeup-reaper').start()


def coordinate(root, *, risks):
    scripts=[('scheduled_risks.py',['--wait-for-check'])] if risks else []
    scripts.append(('scheduled_collection.py',[]))
    codes=[]
    for script,options in scripts:
        # A disabled/paused/failed deep check must not disable baseline's own
        # admission gates. An existing scheduler keeps its lock and finishes first.
        try:
            child=subprocess.Popen([sys.executable,str(root/script),*options],cwd=root)
            codes.append(child.wait())
        except OSError:
            codes.append(1)
    return next((code for code in codes if code),0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--risks',action='store_true')
    args=parser.parse_args()
    raise SystemExit(coordinate(Path(__file__).resolve().parent,risks=args.risks))
