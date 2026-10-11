import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import pytest
from ops.remote_access import Access,handler,COOKIE,TTL,public_origin

@pytest.fixture
def gateway(tmp_path):
    seen=[]
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):self.do_POST()
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            seen.append((self.path,dict(self.headers),body))
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"ok":true}')
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    config=tmp_path/'public.json';config.write_text(json.dumps({'origin':'https://news.example.org'}))
    access=Access(config,'a'*32,upstream.server_port)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(access))
    for s in [upstream,server]:threading.Thread(target=s.serve_forever,daemon=True).start()
    def request(path='/api/context',method='GET',payload=None,cookie='',origin='https://news.example.org',host='news.example.org'):
        c=HTTPConnection('127.0.0.1',server.server_port)
        headers={'Host':host,'Origin':origin,'Content-Type':'application/json','Cookie':cookie}
        c.request(method,path,None if payload is None else json.dumps(payload),headers)
        r=c.getresponse(); result=(r.status,dict(r.getheaders()),r.read());c.close();return result
    yield access,seen,request
    for s in [server,upstream]:s.shutdown();s.server_close()

def login(request):
    status,headers,_=request('/_remote/login','POST',{'password':'a'*32})
    assert status==200
    assert all(x in headers['Set-Cookie'] for x in ['Secure','HttpOnly','SameSite=Strict','Path=/'])
    return headers['Set-Cookie'].split(';')[0]

def test_authentication_origin_and_upstream_boundary(gateway):
    access,seen,r=gateway
    assert r()[0]==401
    assert r('/context')[0]==200
    assert not seen
    assert r('/_remote/login','POST',{'password':'a'*32},origin='https://evil.example')[0]==403
    assert r('/_remote/login','POST',{'password':'wrong'})[0]==401
    cookie=login(r)
    assert r('/api/context?q=a%26b%3Dc',cookie=cookie)[0]==200
    assert seen[-1][0]=='/api/context?q=a%26b%3Dc'
    assert 'Cookie' not in seen[-1][1]
    from urllib.parse import urlencode
    long_path='/api/context?'+urlencode({'q':'가'*2000})
    assert r(long_path,cookie=cookie)[0]==200
    assert seen[-1][0]==long_path
    assert r('/api/graph/ask?workspace=1','POST',{'question':'test'},cookie)[0]==200
    assert seen[-1][1]['Origin']==f'http://127.0.0.1:{access.upstream_port}'
    assert r('/api/graph/ask','POST',{},cookie,origin='null')[0]==403
    assert r(cookie=cookie,host='evil.example')[0]==403
    assert r('//evil.example/path',cookie=cookie)[0]==400
    assert r('/_remote/logout','POST',{},cookie)[0]==200
    assert r(cookie=cookie)[0]==401

def test_expired_session_and_login_throttle(gateway):
    access,seen,r=gateway
    cookie=login(r); access.clock=lambda:10**15
    assert r(cookie=cookie)[0]==401
    for _ in range(10):assert r('/_remote/login','POST',{'password':'wrong'})[0]==401
    assert r('/_remote/login','POST',{'password':'a'*32})[0]==429
    assert not seen

@pytest.mark.parametrize('value',['http://example.org','https://user:pass@example.org','https://example.org/path','https://example.org?token=x','https://example.org#x'])
def test_public_origin_rejects_unsafe_addresses(value):
    with pytest.raises(ValueError):public_origin(value)
