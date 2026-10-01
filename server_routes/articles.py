"""Articles routes; shared transport checks stay in server_http."""

def get(self, route, params):
    if route.path == '/source-link':
        import sqlite3
        from contextlib import closing
        from urllib.parse import quote,urlsplit,urlunsplit
        from source_navigation import original_url
        from url_parser import valid
        value=params.get('url',[''])[0]
        if not valid(value) or len(value)>32768 or urlsplit(value).username:
            self.send_json({'error':'유효한 원출처 URL이 필요합니다.'},400)
            return True
        with closing(sqlite3.connect(self.services.path,timeout=10)) as db:
            target=original_url(db,value)
        if not valid(target) or urlsplit(target).username:
            self.send_json({'error':'원출처 URL을 확인할 수 없습니다.'},400)
            return True
        parsed=urlsplit(target)
        # Preserve existing escapes and all URI delimiters; encode only Unicode
        # and characters that cannot be represented in an HTTP Location header.
        target=urlunsplit((parsed.scheme,parsed.netloc.encode('idna').decode(),parsed.path,parsed.query,parsed.fragment))
        location=quote(target,safe=":/?#[]@!$&'()*+,;=%~-._")
        self.send_response(302)
        self.send_header('Location',location)
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length','0')
        self.end_headers()
        return True
    if route.path == '/paper-context.js':
        content = (self.services.ROOT / 'static' / 'paper-context.js').read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/javascript; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        return True
    if route.path == '/api/article-explanations':
        try:
            self.send_json(self.services.article_service.get(params.get('url', [''])[0]))
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        return True
    if route.path in ('/article', '/article.js'):
        name = 'article.html' if route.path == '/article' else 'article.js'
        content = (self.services.ROOT / 'static' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ('text/html' if name.endswith('html') else 'application/javascript') + '; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        return True
    return False

def post(self, route, payload):
    if route.path == '/api/article-explanations':
        try:
            self.send_json(self.services.article_service.submit(payload.get('url', '')), 202)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        except RuntimeError as error:
            self.send_json({'error': str(error)}, 429)
        return True
    return False
