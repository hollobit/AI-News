"""Real process/thread locks must join equal inputs without serializing others."""
import concurrent.futures
import fcntl
import multiprocessing
from pathlib import Path
import queue
import threading

import pytest

from model_policy import policy
from strategic_workflow import WorkflowService, Paused, digest
from workflow_efficiency import VERSION, cached_call

SCHEMA = {'type': 'object'}


def key(prompt):
    return digest([VERSION, prompt, SCHEMA, policy('technology', prompt=prompt)])


def collision():
    seen = {}
    for number in range(10000):
        prompt = f'ROLE: technology\n독립 근거 {number}'
        prefix = key(prompt)[:2]
        if prefix in seen:
            return seen[prefix], prompt
        seen[prefix] = prompt
    raise AssertionError('No collision fixture')


def service(path, analyzer):
    # Sources are unused; do not construct a real source-fetch service.
    return WorkflowService(path, sources=object(), analyzer=analyzer, enabled=True,
                           recover_interrupted=False)


def call(instance, prompt, run='run'):
    return cached_call(instance, run, 'technology', prompt, SCHEMA, lambda value: value)


def child(path, prompt, entered, release, results):
    def analyze(text, schema):
        entered.put(text)
        if not release.wait(8):
            raise TimeoutError('test release missing')
        return {'text': text}
    instance = service(path, analyze)
    try:
        results.put(('ok', call(instance, prompt)))
    except Exception as exc:
        results.put(('error', type(exc).__name__))
    finally:
        instance.close()


def stop(processes, release):
    if any(process.is_alive() for process in processes):
        release.set()
    for process in processes:
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(3)


def test_colliding_keys_enter_independently_in_threads(tmp_path):
    prompts = collision()
    assert key(prompts[0])[:2] == key(prompts[1])[:2]
    barrier = threading.Barrier(2)
    calls = []
    def analyze(prompt, schema):
        calls.append(prompt)
        barrier.wait(3)
        return {'text': prompt}
    instance = service(tmp_path/'db', analyze)
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(call, instance, prompt, str(i)) for i, prompt in enumerate(prompts)]
            assert [job.result(5) for job in jobs] == [{'text': p} for p in prompts]
        assert sorted(calls) == sorted(prompts)
    finally:
        instance.close()


@pytest.mark.parametrize('same_input', [False, True])
def test_processes_only_join_exact_inputs(tmp_path, same_input):
    prompts = collision()
    if same_input:
        prompts = (prompts[0], prompts[0])
    path = tmp_path/'db'
    service(path, lambda *_: None).close()
    ctx = multiprocessing.get_context('spawn')
    entered, results, release = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [ctx.Process(target=child, args=(path, p, entered, release, results)) for p in prompts]
    try:
        for process in processes:
            process.start()
        first = entered.get(timeout=5)
        if same_input:
            with pytest.raises(queue.Empty):
                entered.get(timeout=.3)
        else:
            assert {first, entered.get(timeout=5)} == set(prompts)
        release.set()
        values = [results.get(timeout=5), results.get(timeout=5)]
        assert all(result[0] == 'ok' for result in values), values
        assert sorted(result[1]['text'] for result in values) == sorted(prompts)
        with pytest.raises(queue.Empty):
            entered.get(timeout=.15)
    finally:
        stop(processes, release)
    assert all(process.exitcode == 0 for process in processes)


def test_legacy_exclusive_owner_blocks_new_owner_until_release(tmp_path):
    prompt = collision()[0]
    path = tmp_path/'db'
    instance = service(path, lambda text, _: {'text': text})
    directory = Path(str(path)+'.stage-locks')
    directory.mkdir()
    entered = threading.Event()
    instance.analyzer = lambda text, _: (entered.set() or {'text': text})
    try:
        with (directory/(key(prompt)[:2]+'.lock')).open('a') as old:
            fcntl.flock(old, fcntl.LOCK_EX)
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                job = pool.submit(call, instance, prompt)
                assert not entered.wait(.2)
                fcntl.flock(old, fcntl.LOCK_UN)
                assert job.result(3) == {'text': prompt}
        # Stable files are retained; deleting a locked inode breaks single-flight.
        assert (directory/'keys'/key(prompt)[:2]/(key(prompt)+'.lock')).is_file()
    finally:
        instance.close()


def test_new_owner_shared_stripe_blocks_old_exclusive_owner(tmp_path):
    prompt = collision()[0]
    entered, release = threading.Event(), threading.Event()
    def analyze(text, _):
        entered.set()
        assert release.wait(3)
        return {'text': text}
    instance = service(tmp_path/'db', analyze)
    try:
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            job = pool.submit(call, instance, prompt)
            assert entered.wait(2)
            stripe = Path(str(instance.path)+'.stage-locks')/(key(prompt)[:2]+'.lock')
            try:
                with stripe.open('a') as old:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(old, fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                release.set()
            assert job.result(3) == {'text': prompt}
    finally:
        release.set()
        instance.close()


def test_waiting_owner_honors_pause_and_does_not_invoke_model(tmp_path):
    prompt = collision()[0]
    calls = []
    instance = service(tmp_path/'db', lambda *_: calls.append('unexpected'))
    checked = threading.Event()
    original_check = instance._check
    checks = []
    def check():
        original_check()
        checks.append(True)
        if len(checks) >= 3:
            checked.set()
    instance._check = check
    keys = Path(str(instance.path)+'.stage-locks')/'keys'/key(prompt)[:2]
    keys.mkdir(parents=True)
    with (keys/(key(prompt)+'.lock')).open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            job = pool.submit(call, instance, prompt)
            assert checked.wait(2)
            instance.close()
            with pytest.raises(Paused):
                job.result(2)
    assert calls == []


def test_process_death_releases_exact_lock_without_deleting_inode(tmp_path):
    path = tmp_path/'db'
    service(path, lambda *_: None).close()
    prompt = collision()[0]
    ctx = multiprocessing.get_context('spawn')
    entered, results, release = ctx.Queue(), ctx.Queue(), ctx.Event()
    owner = ctx.Process(target=child, args=(path, prompt, entered, release, results))
    owner.start()
    try:
        assert entered.get(timeout=5) == prompt
        lock = Path(str(path)+'.stage-locks')/'keys'/key(prompt)[:2]/(key(prompt)+'.lock')
        inode = lock.stat().st_ino
        owner.terminate()
        owner.join(3)
        assert not owner.is_alive()
        instance = service(path, lambda text, _: {'text': text})
        try:
            assert call(instance, prompt) == {'text': prompt}
        finally:
            instance.close()
        assert lock.stat().st_ino == inode
    finally:
        stop([owner], release)


def test_failed_call_releases_lock_and_is_not_cached(tmp_path):
    prompt = collision()[0]
    def fail(*_):
        raise ValueError('provider failed')
    instance = service(tmp_path/'db', fail)
    try:
        with pytest.raises(ValueError, match='provider failed'):
            call(instance, prompt)
        instance.analyzer = lambda text, _: {'text': text}
        assert call(instance, prompt) == {'text': prompt}
        with instance.db() as db:
            assert db.execute('SELECT COUNT(*) FROM workflow_stage_cache').fetchone()[0] == 1
    finally:
        instance.close()
