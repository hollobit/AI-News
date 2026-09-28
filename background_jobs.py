"""A server-owned monitor for a database-owned detached background worker."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def paths(db):
    base = str(Path(db).resolve()) + '.background-worker'
    return Path(base + '.lock'), Path(base + '.json')


def worker_status(db):
    lock, state = paths(db)
    if not lock.exists():
        return {'status': 'stopped'}
    with lock.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                result = json.loads(state.read_text())
            except (OSError, ValueError):
                return {'status': 'preparing'}
            try:
                os.kill(result['pid'], 0)
            except ProcessLookupError:
                return {'status': 'preparing'}
            return dict(result, status='running')
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            return {'status': 'stopped'}


class BackgroundJobs:
    def __init__(self, db, *, monitor=True):
        self.db = str(Path(db).resolve())
        self.stop = threading.Event()
        self.thread = None
        self.ensure()
        if monitor:
            self.thread = threading.Thread(target=self._monitor, daemon=True, name='background-worker-monitor')
            self.thread.start()

    def ensure(self):
        lock, state = paths(self.db)
        with Path(str(lock) + '.launch').open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            status = worker_status(self.db)
            if status['status'] in ('running', 'preparing'):
                return status
            root = Path(__file__).resolve().parent
            directory = root / '.runtime' / 'background-worker'
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / 'worker.log').open('ab') as log:
                child = subprocess.Popen([sys.executable, str(root / 'background_worker.py'), '--db', self.db],
                    cwd=root, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                status = worker_status(self.db)
                if status['status'] == 'running' and status['pid'] == child.pid:
                    return status
                if child.poll() is not None:
                    raise RuntimeError('백그라운드 worker 준비 실패')
                time.sleep(.05)
            # Do not kill/relaunch a process that may already own durable jobs.
            raise RuntimeError('백그라운드 worker 준비 지연; 소유 상태 확인 필요')

    def _monitor(self):
        failures = 0
        while not self.stop.wait(min(60, 10 * 2 ** failures)):
            try:
                self.ensure()
                failures = 0
            except Exception:
                failures += 1
                if failures >= 3:
                    return

    def status(self):
        return worker_status(self.db)

    def close(self):
        # The worker and its model calls survive HTTP shutdown.
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
