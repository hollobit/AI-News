"""Supervise authenticated ingress and a temporary HTTPS tunnel on this Mac.

The public origin is the only published runtime field. Cloudflared's temporary
hostname changes on restart; Pages' existing sync publishes the new entry link.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import select
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from queue import Empty, SimpleQueue
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.runtime/remote-access'
children = []
stopping = False


def ready_tunnel_origin(output):
    """Accept an issued hostname only after a tunnel connection is registered.

    Cloudflared error messages contain api.trycloudflare.com/tunnel, which is
    the provisioning endpoint, not a site visitors can log into.
    """
    if 'Registered tunnel connection' not in output:
        return ''
    for match in re.finditer(r'https://([a-z0-9-]+)\.trycloudflare\.com(?=[\s|"\']|$)', output):
        if match.group(1) not in ('api', 'www'):
            return match.group()
    return ''


def dns_health(origin, resolver=socket.getaddrinfo):
    """Only a missing origin with working general DNS warrants a new tunnel."""
    try:
        resolver(urlsplit(origin).hostname, 443)
        return True
    except socket.gaierror as error:
        if error.errno != socket.EAI_NONAME:
            return None
    except OSError:
        return None
    try:
        resolver('www.cloudflare.com', 443)
    except OSError:
        return None
    return False


class OriginHealth:
    """One nonblocking DNS probe at a time, with two confirmed failures."""
    def __init__(self, probe=dns_health, clock=time.monotonic):
        self.probe, self.clock = probe, clock
        self.results = SimpleQueue()
        self.worker = None
        self.watch('')

    def watch(self, origin):
        self.origin, self.failures = origin, 0
        self.next_at = self.clock() + 30

    def _check(self, origin):
        self.results.put((origin, self.probe(origin)))

    def poll(self):
        try:
            while True:
                origin, healthy = self.results.get_nowait()
                if origin == self.origin:
                    self.failures = self.failures + 1 if healthy is False else 0
        except Empty:
            pass
        if self.failures >= 2:
            return True
        if self.origin and self.clock() >= self.next_at and (not self.worker or not self.worker.is_alive()):
            self.next_at = self.clock() + 60
            self.worker = threading.Thread(target=self._check, args=(self.origin,), daemon=True)
            self.worker.start()
        return False


def stop(*_):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


def publish(origin):
    from remote_access import public_origin
    origin = public_origin(origin)
    temporary = STATE / 'public.tmp'
    temporary.write_text(json.dumps({'origin':origin,'mode':'temporary'}))
    temporary.replace(STATE/'public.json')
    print(json.dumps({'status':'connected','origin':origin}), flush=True)


def main():
    os.chdir(ROOT)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    password = STATE/'password.txt'
    if not password.exists():
        fd = os.open(password, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32)+'\n')
    lock = open(STATE/'service.lock', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    try:
        gateway = subprocess.Popen([sys.executable,str(ROOT/'ops/remote_access.py')])
        children.append(gateway)
        time.sleep(1)
        if gateway.poll() is not None:
            raise RuntimeError('Remote ingress failed to start; check port 8002')
        health = OriginHealth()
        while not stopping:
            (STATE/'public.json').unlink(missing_ok=True)
            tunnel = subprocess.Popen([str(STATE/'cloudflared'),'tunnel','--no-autoupdate',
                '--protocol','http2','--url','http://127.0.0.1:8002'],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
            children.append(tunnel)
            published = False
            output = ''
            while tunnel.poll() is None:
                if stopping or gateway.poll() is not None:
                    tunnel.terminate();break
                if published and health.poll():
                    print('{"status":"reconnecting","reason":"origin_dns_missing"}',flush=True)
                    tunnel.terminate();break
                ready,_,_ = select.select([tunnel.stdout],[],[],1)
                if ready:
                    output = (output+os.read(tunnel.stdout.fileno(),65536).decode(errors='replace'))[-65536:]
                    origin = ready_tunnel_origin(output)
                    if origin and not published:
                        publish(origin);health.watch(origin);published = True
            tunnel.wait()
            children.remove(tunnel)
            if not stopping and gateway.poll() is not None:
                raise RuntimeError('Remote ingress stopped')
            if not stopping:
                print('{"status":"reconnecting"}',flush=True)
                time.sleep(10)
    finally:
        stop()
        for child in children:
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        (STATE/'public.json').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
