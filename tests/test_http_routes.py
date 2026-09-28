from dataclasses import fields, replace
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import json
import threading
import sqlite3
from unittest.mock import patch
import pytest
from server_context import ServerContext
from server_http import make_handler


@pytest.fixture
def server(tmp_path):
    calls = []
    sqlite3.connect(tmp_path/'news.db').close()
    service = SimpleNamespace(enabled=True, get=lambda value='': {'id': value, 'status': 'missing'},
        submit=lambda url: calls.append(url) or {'status': 'queued'}, list=lambda: [],
        status=lambda: {'status': 'ready'}, worker_counts=lambda: {}, request=lambda window, expanded: {'window':window,'expanded':expanded})
    ctx = ServerContext(**{f.name: None for f in fields(ServerContext)})
    ctx = replace(ctx, ROOT=Path.cwd(), path=str(tmp_path/'news.db'), port=0, article_service=service,
        baseline_service=service, workflow_service=service, observatory_service=service)
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(ctx))
    server.RequestHandlerClass.services = replace(ctx, port=server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    yield server, calls
    server.shutdown();server.server_close();thread.join()


def request(server, method, path, body=None, headers=None):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    connection.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers or {})
    response = connection.getresponse();status=response.status;data=response.read();headers=dict(response.getheaders());connection.close()
    return status,data,headers


def test_split_routes_assets_validation_and_dispatch(server):
    http,calls=server
    for path in ('/operations','/strategy-baseline.js','/strategy-topics.js','/article','/paper-context.js'):
        status,body,headers=request(http,'GET',path)
        assert status==200 and body and headers['Content-Length']==str(len(body))
    assert request(http,'GET','/missing')[0]==404
    status,body,_=request(http,'GET','/api/observatory?window=30&expanded=1')
    assert status==200 and json.loads(body)['window']==30
    headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{http.server_port}'}
    assert request(http,'POST','/api/article-explanations',{'url':'https://example.org'},headers)[0]==202
    assert calls==['https://example.org']
    bad={**headers,'Origin':'https://untrusted.example'}
    assert request(http,'POST','/api/article-explanations',{'url':'https://example.org'},bad)[0]==403
    assert len(calls)==1
    assert request(http,'POST','/api/article-explanations',[],headers)[0]==400
    assert request(http,'POST','/api/unknown',{},headers)[0]==404


def test_compact_status_routes_precede_full_run_and_preserve_etag(server):
    http,_=server
    with patch('status_views.status_response',return_value={'runs':[],'version':'compact-v1'}) as compact:
        status,body,headers=request(http,'GET','/api/baseline?view=status')
        assert status==200 and json.loads(body)['version']=='compact-v1'
        assert headers['ETag']=='"compact-v1"' and compact.call_count==1
        assert request(http,'GET','/api/baseline?view=status',headers={'If-None-Match':'"compact-v1"'})[0]==304
