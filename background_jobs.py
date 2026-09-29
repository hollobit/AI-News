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


def _worker_status(db):
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
            age = time.time() - result.get('heartbeat_at', time.time())
            return dict(result, status='running', heartbeat_age_seconds=round(age,1),
                        health='unresponsive' if age > 15 else 'observed' if 'heartbeat_at' in result else 'legacy')
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            return {'status': 'stopped'}


def worker_status(db):
    value = _worker_status(db)
    monitor_file = Path(str(paths(db)[1]) + '.monitor.json')
    try:
        monitor = json.loads(monitor_file.read_text())
        try: os.kill(monitor['pid'], 0)
        except ProcessLookupError: monitor = dict(monitor, state='stopped')
        value['monitor'] = monitor
    except (OSError, ValueError, KeyError):
        pass
    return value


class BackgroundJobs:
    def __init__(self, db, *, monitor=True):
        self.db = str(Path(db).resolve())
        self.stop = threading.Event()
        self.thread = None
        self.monitor_file = Path(str(paths(self.db)[1]) + '.monitor.json')
        self.monitor_state = {'pid':os.getpid(),'state':'starting','failures':0}
        self._record_monitor()
        try:
            self.ensure()
        except Exception as error:
            self._record_monitor(state='failed', error_type=type(error).__name__)
            raise
        self._record_monitor(state='running' if monitor else 'disabled')
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

    def _record_monitor(self, **fields):
        from worker_health import write_state
        self.monitor_state.update(fields, checked_at=time.time())
        write_state(self.monitor_file,self.monitor_state)

    def _monitor(self):
        failures = 0
        while not self.stop.wait(min(60, 10 * 2 ** failures)):
            try:
                self.ensure()
                failures = 0
                self._record_monitor(state='running', failures=0, error_type=None)
            except Exception as error:
                failures += 1
                self._record_monitor(state='failed' if failures>=3 else 'retrying',
                    failures=failures,error_type=type(error).__name__)
                if failures >= 3:
                    return

    def status(self):
        return dict(worker_status(self.db), monitor=dict(self.monitor_state))

    def close(self):
        # The worker and its model calls survive HTTP shutdown.
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
        try:
            current=json.loads(self.monitor_file.read_text())
            if current.get('pid')==os.getpid():self._record_monitor(state='stopped')
        except (OSError,ValueError):pass
