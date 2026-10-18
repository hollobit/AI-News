"""Authenticated HTTPS ingress to the existing loopback news server.

Run behind a TLS tunnel/reverse proxy. No database, model or collector is copied.
Only the fixed loopback upstream is reachable; browser credentials stop here.
"""
import argparse
from collections import defaultdict, deque
import hashlib
import hmac
from http.client import HTTPConnection, HTTPException
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

COOKIE = '__Host-news-session'
TTL = 8 * 3600


class RemoteHTTPServer(ThreadingHTTPServer):
    # The tunnel forwards parallel browser asset requests over separate sockets.
    # Match the local server's backlog so a page load survives a brief accept lag.
    request_queue_size = 64


LOGIN = '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>원격 분석실 로그인</title>
<style>body{font:16px system-ui;background:#edf2f8;color:#17334a;margin:0}main{max-width:420px;margin:12vh auto;padding:28px;background:white;border-radius:18px}input,button{box-sizing:border-box;width:100%;padding:14px;margin:12px 0;border:1px solid #bbcadb;border-radius:8px;font:inherit}button{background:#204c70;color:white}p{line-height:1.7}#status{color:#a13030}</style>
<main><h1>원격 통합 분석실</h1><p>관계 탐색·자연어 분석·전망 생성과 운영 기능을 사용하려면 소유자의 접속 암호를 입력하세요.</p>
<form id="login"><label for="password">접속 암호</label><input id="password" type="password" autocomplete="current-password" required autofocus maxlength="128"><button>로그인</button></form><p id="status" role="status"></p><p>로그인은 최대 8시간 유지됩니다.</p></main>
<script>document.querySelector('#login').onsubmit=async e=>{e.preventDefault();const s=document.querySelector('#status');s.textContent='확인 중…';try{const r=await fetch('/_remote/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.querySelector('#password').value})});if(!r.ok)throw Error(r.status===429?'잠시 후 다시 시도해 주세요.':'암호를 확인해 주세요.');location.reload();}catch(e){s.textContent=e.message;}};</script></html>'''.encode()


def public_origin(value):
    u = urlsplit(value)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password
            or u.path not in ('', '/') or u.query or u.fragment or u.port not in (None, 443)):
        raise ValueError('A bare HTTPS public origin is required')
    return 'https://' + u.netloc.lower()


class Access:
    def __init__(self, config, password, upstream_port=8001, clock=time.monotonic):
        self.config, self.upstream_port, self.clock = Path(config), upstream_port, clock
        self.password_hash = hashlib.sha256(password.encode()).digest()
        self.sessions = {}
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()

    def origin(self):
        try:
            return public_origin(json.loads(self.config.read_text())['origin'])
        except (OSError, ValueError, KeyError):
            return ''

    def permitted(self, key, maximum, seconds):
        now = self.clock()
        with self.lock:
            # Bound unauthenticated visitor state as well as individual rates.
            if len(self.attempts) > 4096:
                self.attempts = defaultdict(deque, {k:v for k,v in self.attempts.items() if v and now-v[-1]<900})
                if len(self.attempts) > 4096:
                    return False
            q = self.attempts[key]
            while q and now - q[0] >= seconds:
                q.popleft()
            if len(q) >= maximum:
                return False
            q.append(now)
            return True

    def login(self, password):
        if not isinstance(password, str) or not hmac.compare_digest(
                self.password_hash, hashlib.sha256(password.encode()).digest()):
            return None
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.sessions = {k:v for k,v in self.sessions.items() if v>now}
            if len(self.sessions) >= 32:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[token] = now + TTL
        return token

    def session(self, cookie):
        try:
            c = SimpleCookie(cookie or '')
            token = c[COOKIE].value
        except (KeyError, ValueError, CookieError):
            return ''
        with self.lock:
            if self.sessions.get(token, 0) > self.clock():
                return token
            self.sessions.pop(token, None)
        return ''


def handler(access):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'NewsRemote'

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, *args):
            pass  # Never log queries, cookies, passwords or upstream bodies.

        def reply(self, status, body=b'', mime='application/json; charset=utf-8', extra=()):
            self.send_response(status)
            for key, value in [('Content-Type', mime), ('Content-Length', str(len(body))),
                               ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                               ('Referrer-Policy', 'no-referrer'), ('X-Frame-Options', 'DENY'),
                               ('Strict-Transport-Security', 'max-age=31536000'),
                               ('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]+list(extra):
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def error(self, status, message):
            self.reply(status, json.dumps({'error':message}, ensure_ascii=False).encode())

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            origin = access.origin()
            if not origin:
                self.error(503, '원격 접속 준비 중입니다.'); return
            # A configured external Host is mandatory even for a loopback tunnel.
            if self.headers.get('Host', '').lower() != urlsplit(origin).netloc:
                self.error(403, '허용되지 않은 접속 주소입니다.'); return
            raw_target = self.requestline.split(' ')[1]
            # 2,000 Korean characters can occupy 18 KB after URL encoding.
            if not self.path.startswith('/') or raw_target.startswith('//') or len(self.path)>24576:
                self.error(400, '올바르지 않은 주소입니다.'); return
            path = urlsplit(self.path).path
            body = None
            if self.command == 'POST':
                if self.headers.get('Origin') != origin or self.headers.get_content_type() != 'application/json':
                    self.error(403, '로그인한 페이지에서 요청해 주세요.'); return
                try:
                    if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                        raise ValueError
                    size = int(self.headers['Content-Length'])
                    if not 0 < size <= 32768:
                        raise ValueError
                    body = self.rfile.read(size)
                    if len(body) != size:
                        raise ValueError
                except (ValueError, TimeoutError):
                    self.error(400, '요청 크기가 올바르지 않습니다.'); return
            if path == '/_remote/login' and self.command == 'POST':
                ip = self.headers.get('CF-Connecting-IP') or self.client_address[0]
                if not access.permitted(('login', ip), 10, 900):
                    self.error(429, '로그인 시도가 많습니다. 15분 후 다시 시도하세요.'); return
                try:
                    payload = json.loads(body)
                    token = access.login(payload.get('password')) if isinstance(payload, dict) else None
                except (ValueError, UnicodeError):
                    token = None
                if not token:
                    self.error(401, '암호를 확인해 주세요.'); return
                self.reply(200, b'{"ok":true}', extra=[('Set-Cookie',f'{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={TTL}')]); return
            token = access.session(self.headers.get('Cookie'))
            if not token:
                if self.command == 'GET' and not path.startswith('/api/'):
                    self.reply(200, LOGIN, 'text/html; charset=utf-8')
                else:
                    self.error(401, '로그인이 필요하거나 만료되었습니다. 새로고침 후 로그인해 주세요.')
                return
            if path == '/_remote/logout' and self.command == 'POST':
                with access.lock:
                    access.sessions.pop(token, None)
                self.reply(200, b'{"ok":true}', extra=[('Set-Cookie',f'{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0')]); return
            if self.command == 'POST' and not access.permitted(('post',token), 30, 60):
                self.error(429, '요청이 많습니다. 잠시 후 다시 시도하세요.'); return
            conn = HTTPConnection('127.0.0.1', access.upstream_port, timeout=120)
            headers = {'Host': f'127.0.0.1:{access.upstream_port}'}
            if self.command == 'POST':
                headers.update(Origin=f'http://127.0.0.1:{access.upstream_port}', **{'Content-Type':'application/json'})
            try:
                conn.request(self.command, '/context' if self.path=='/' else self.path, body=body, headers=headers)
                r = conn.getresponse()
                data = r.read()
                if r.getheader('Content-Type','').startswith('text/html'):
                    # Session control belongs to ingress, never the local server.
                    control = b'''<script>(()=>{const a=document.createElement('aside');a.style.cssText='position:sticky;bottom:0;background:#17334a;color:white;padding:10px;text-align:right;z-index:1000';const b=document.createElement('button');b.textContent='\\uC6D0\\uACA9 \\uC811\\uC18D \\uB85C\\uADF8\\uC544\\uC6C3';b.onclick=async()=>{await fetch('/_remote/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});location.reload();};a.append(b);document.body.append(a);})();</script>'''
                    data = data.replace(b'</body>', control+b'</body>')
                extra = [(k,v) for k,v in r.getheaders() if k.lower() in {'content-disposition','etag','retry-after'}]
                self.reply(r.status, data, r.getheader('Content-Type','application/octet-stream'), extra)
            except (OSError, TimeoutError, HTTPException):
                self.error(503, '분석 서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.')
            finally:
                conn.close()
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='.runtime/remote-access/public.json')
    p.add_argument('--password-file', default='.runtime/remote-access/password.txt')
    p.add_argument('--port', type=int, default=8002)
    p.add_argument('--upstream-port', type=int, default=8001)
    args = p.parse_args()
    password = Path(args.password_file).read_text().strip()
    if len(password)<24:
        raise SystemExit('Use a generated access password of at least 24 characters')
    server = RemoteHTTPServer(('127.0.0.1',args.port), handler(Access(args.config,password,args.upstream_port)))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    main()
