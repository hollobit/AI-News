"""Small process-local answer cache; callers key it by current retrieved evidence."""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
import threading
import time

_LOCK = threading.Lock()
_CACHE = OrderedDict()
_FLIGHTS = {}


def reuse_answer(key, build):
    if key is None:return build(), False
    with _LOCK:
        if key in _CACHE:
            stamp,value=_CACHE[key]
            if time.monotonic()-stamp<600:
                _CACHE.move_to_end(key)
                return deepcopy(value), True
            del _CACHE[key]
        owner=key not in _FLIGHTS
        future=_FLIGHTS.setdefault(key,Future())
    if not owner:return deepcopy(future.result()), True
    try:
        value=build()
        with _LOCK:
            _CACHE[key]=(time.monotonic(),deepcopy(value))
            while len(_CACHE)>32:_CACHE.popitem(last=False)
        future.set_result(deepcopy(value))
        return value,False
    except BaseException as error:
        future.set_exception(error)
        raise
    finally:
        with _LOCK:_FLIGHTS.pop(key,None)
