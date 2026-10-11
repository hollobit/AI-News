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
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.runtime/remote-access'
children = []
stopping = False


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
                ready,_,_ = select.select([tunnel.stdout],[],[],1)
                if ready:
                    output = (output+os.read(tunnel.stdout.fileno(),65536).decode(errors='replace'))[-65536:]
                    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com\b',output)
                    if match and not published:
                        publish(match.group());published = True
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
