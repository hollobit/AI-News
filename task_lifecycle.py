"""Cooperative cancellation for disposable views; durable job owners are separate."""
from concurrent.futures import CancelledError, ThreadPoolExecutor, wait
from contextlib import contextmanager
import threading

_CONTEXT = threading.local()


def checkpoint():
    event = getattr(_CONTEXT, 'stop', None)
    if event is not None and event.is_set():
        raise CancelledError('View preparation stopped')


@contextmanager
def cancellation_scope(event):
    previous = getattr(_CONTEXT, 'stop', None)
    _CONTEXT.stop = event
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _CONTEXT.stop = previous


def cancellable_db(db):
    event = getattr(_CONTEXT, 'stop', None)
    if event is not None:
        db.execute('PRAGMA busy_timeout=1000')
        db.set_progress_handler(lambda: int(event.is_set()), 1000)
    return db


class PreparationExecutor(ThreadPoolExecutor):
    """Cancel queued work and drain running work at its next read checkpoint."""
    def __init__(self, max_workers=1, **kwargs):
        super().__init__(max_workers=max_workers, **kwargs)
        self.stop = threading.Event()
        self.futures = set()
        self.guard = threading.Lock()
        self.drain_status = None

    def submit(self, fn, /, *args, **kwargs):
        def invoke():
            with cancellation_scope(self.stop):
                return fn(*args, **kwargs)
        future = super().submit(invoke)
        with self.guard:
            self.futures.add(future)
        def done(completed):
            with self.guard:
                self.futures.discard(completed)
        future.add_done_callback(done)
        return future

    def shutdown(self, wait=False, *, cancel_futures=True, deadline=3):
        self.stop.set()
        super().shutdown(wait=False, cancel_futures=cancel_futures)
        with self.guard:
            futures = set(self.futures)
        if futures:
            _, pending = globals()['wait'](futures, timeout=deadline)
        else:
            pending = set()
        self.drain_status = {'status': 'drained' if not pending else 'draining', 'pending': len(pending)}
        return self.drain_status


def close_services(services):
    """One failed close must not skip the remaining resource owners."""
    errors = []
    for service in services:
        try:
            service.close()
        except Exception as error:
            errors.append({'service': type(service).__name__, 'error_type': type(error).__name__})
    return errors


class ServiceScope:
    """Close partially initialized bootstraps as well as normal server sessions."""
    def __init__(self):self.services=[];self.errors=[]
    def register(self,service):
        self.services.append(service)
        return service
    def close(self):
        services,self.services=self.services,[]
        self.errors.extend(close_services(reversed(services)))
        return self.errors
    def __enter__(self):return self
    def __exit__(self,*exception):self.close()
