import threading
import time
from concurrent.futures import CancelledError
import pytest
from task_lifecycle import PreparationExecutor,checkpoint,close_services


def test_running_and_queued_views_cancel_before_publishing():
    entered=threading.Event();published=[]
    def slow():
        entered.set()
        while True:
            checkpoint();time.sleep(.005)
        published.append(True)
    pool=PreparationExecutor(1)
    running=pool.submit(slow);assert entered.wait(1)
    queued=pool.submit(published.append,True)
    start=time.monotonic();status=pool.shutdown(deadline=1)
    assert time.monotonic()-start<1 and status=={'status':'drained','pending':0}
    with pytest.raises(CancelledError):running.result()
    assert queued.cancelled() and published==[]


def test_all_resources_close_even_after_one_failure():
    closed=[]
    class Service:
        def __init__(self,name):self.name=name
        def close(self):
            closed.append(self.name)
            if self.name=='bad':raise RuntimeError('not exposed')
    errors=close_services([Service('first'),Service('bad'),Service('last')])
    assert closed==['first','bad','last']
    assert errors==[{'service':'Service','error_type':'RuntimeError'}]


def test_partial_initialization_closes_registered_services_once():
    from task_lifecycle import ServiceScope
    closed=[]
    class Service:
        def __init__(self,name):self.name=name
        def close(self):closed.append(self.name)
    with pytest.raises(ValueError):
        with ServiceScope() as scope:
            scope.register(Service('first'))
            scope.register(Service('second'))
            raise ValueError('later constructor failed')
    scope.close()
    assert closed==['second','first']
