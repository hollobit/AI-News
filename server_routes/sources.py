"""Sources routes; shared transport checks stay in server_http."""
import sqlite3
from urllib.parse import parse_qs

def get(self, route, params):
    if route.path in ('/sources', '/sources.js', '/sources.css'):
        name = 'sources.html' if route.path == '/sources' else route.path[1:]
        content = (self.services.ROOT / 'static' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css'}[name.rsplit('.', 1)[1]] + '; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        return True
    if route.path == '/api/agent-reach':
        self.send_json(self.services.reach_service.status())
        return True
    if route.path == '/api/agent-reach/pipeline':
        self.send_json(self.services.reach_pipeline.status())
        return True
    if route.path == '/api/agent-reach/passages':
        from source_store import search
        from link_groups import canonical_url
        query = params.get('q', [''])[0][:500]
        url = canonical_url(params.get('url', [''])[0])
        with sqlite3.connect(self.services.path, timeout=2) as db:
            self.send_json({'evidence': search(db, query, urls=[url])})
        return True
    if route.path == '/api/agent-reach/source':
        from source_store import detail
        with sqlite3.connect(self.services.path) as db:
            self.send_json(detail(db, params.get('url', [''])[0]))
        return True
    if route.path.startswith('/api/agent-reach/tasks/'):
        result = self.services.reach_pipeline.get(route.path.rsplit('/', 1)[-1])
        self.send_json(result or {'error': '작업 없음'}, 200 if result else 404)
        return True
    if route.path.startswith('/api/agent-reach/jobs/'):
        result = self.services.reach_service.get(route.path.rsplit('/', 1)[-1])
        self.send_json(result or {'error': '읽기 작업을 찾을 수 없습니다.'}, 200 if result else 404)
        return True
    if route.path == '/api/sources':
        self.send_json(self.services.source_service.status())
        return True
    return False

def post(self, route, payload):
    is_reach = route.path in ('/api/wiki', '/api/article-explanations', '/api/agent-reach/read', '/api/agent-reach/repair', '/api/agent-reach/subscriptions', '/api/agent-reach/subscription-toggle')
    is_source = route.path == '/api/sources'
    if is_reach:
        try:
            if route.path.endswith('/repair'):
                result = self.services.reach_pipeline.repair(payload.get('limit', 50))
            elif route.path.endswith('/subscriptions'):
                result = self.services.reach_pipeline.add_subscription(payload)
            elif route.path.endswith('/subscription-toggle'):
                result = self.services.reach_pipeline.toggle(payload.get('id'), payload.get('enabled'))
            else:
                result = self.services.reach_service.submit(payload.get('url'), payload.get('mode', 'auto'))
            self.send_json(result, 202)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        return True
    if is_source:
        try:
            db = self.services.connect(self.services.path)
            try:
                from strategy import select_strategy_items
                params = parse_qs(route.query)
                selected = select_strategy_items(db, params)
                requested = payload.get('url')
                urls = list(dict.fromkeys((item['source_url'] for item in selected if item.get('source_url'))))
                if requested:
                    if requested not in urls:
                        raise ValueError('현재 뉴스 범위에 포함된 URL을 선택해 주세요.')
                    urls = [requested]
                limit = max(1, min(100, int(payload.get('limit', 40))))
            finally:
                db.close()
            self.send_json(dict(self.services.source_service.submit(urls[:limit]), available=len(urls)), 202)
        except (ValueError, RuntimeError) as error:
            self.send_json({'error': str(error)}, 400)
        return True
    return False
