"""Collector/worker notification; scheduler locks coalesce duplicate wakeups."""
from pathlib import Path
import subprocess
import sys
import threading


def notify(path, *, risks=True):
    root=Path(__file__).resolve().parent
    # Production schedulers are bound to this DB. Tests/isolated DBs cannot
    # accidentally enqueue work against the production corpus.
    if Path(path).resolve()!=root/'data/news.sqlite3':return
    scripts=['scheduled_collection.py']+(['scheduled_risks.py'] if risks else [])
    for script in scripts:
        process=subprocess.Popen([sys.executable,str(root/script)],cwd=root,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        threading.Thread(target=process.wait,daemon=True,name='pipeline-wakeup-reaper').start()
