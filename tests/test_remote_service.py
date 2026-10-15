import socket
import threading

from ops.remote_service import OriginHealth, dns_health


def test_dns_expiry_requires_missing_origin_and_working_network():
    def resolve(host, port):
        if host == 'gone.trycloudflare.com':
            raise socket.gaierror(socket.EAI_NONAME, 'missing')
        return [(None,)]
    assert dns_health('https://gone.trycloudflare.com', resolve) is False
    assert dns_health('https://live.trycloudflare.com', resolve) is True
    for code in (socket.EAI_AGAIN, socket.EAI_NONAME):
        def offline(*args):raise socket.gaierror(code, 'offline')
        assert dns_health('https://gone.trycloudflare.com', offline) is None


def test_only_consecutive_current_origin_failures_request_reconnection():
    health=OriginHealth(clock=lambda:0)
    health.watch('https://new.trycloudflare.com')
    health.results.put(('https://old.trycloudflare.com',False))
    assert not health.poll() and health.failures==0
    for result,expected in [(False,False),(None,False),(False,False),(True,False),(False,False),(False,True)]:
        health.results.put((health.origin,result))
        assert health.poll() is expected
    health.watch('https://next.trycloudflare.com')
    assert not health.poll() and health.failures==0


def test_slow_probe_does_not_block_supervisor_or_spawn_more_probes():
    now=[0];started=threading.Event();release=threading.Event();calls=[]
    def slow(origin):
        calls.append(origin);started.set();release.wait(2);return True
    health=OriginHealth(probe=slow,clock=lambda:now[0]);health.watch('https://live.trycloudflare.com')
    try:
        now[0]=31;assert not health.poll();assert started.wait(1)
        now[0]=1000
        for _ in range(5):assert not health.poll()
        assert len(calls)==1
    finally:
        release.set()
        health.worker.join(2)
