"""Articles routes; shared transport checks stay in server_http."""

def get(self, route, params):
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
