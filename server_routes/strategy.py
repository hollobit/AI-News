"""Strategy routes; shared transport checks stay in server_http."""
import json
import re
import sqlite3
from urllib.parse import parse_qs, unquote

def get(self, route, params):
    if route.path == '/api/observatory/events':
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_json({'error': '동일 사이트에서만 연결할 수 있습니다.'}, 403)
            return True
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Connection', 'close')
        self.end_headers()
        import time
        last = self.headers.get('Last-Event-ID', '')
        try:
            for _ in range(10):
                try:
                    payload = self.services.observatory_service.events()
                    if payload['version'] != last:
                        last = payload['version']
                        message = 'id: ' + last + '\nevent: processing\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'
                    else:
                        message = ': heartbeat\n\n'
                except sqlite3.OperationalError:
                    message = ': database-busy\n\n'
                self.wfile.write(message.encode())
                self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True
        return True
    if route.path == '/api/observatory/status':
        self.send_json(self.services.observatory_service.status())
        return True
    if route.path == '/api/observatory/history':
        try:
            self.send_json(self.services.observatory_service.events())
        except sqlite3.OperationalError:
            self.send_json({'error': '처리 기록 갱신 중입니다.'}, 503)
        return True
    if route.path == '/api/observatory':
        try:
            expanded = params.get('expand', ['0'])[0].lower() in {'1', 'true', 'yes', 'expanded'}
            result = self.services.observatory_service.request(int(params.get('window', ['14'])[0]), expanded)
            self.send_json(result, 202 if result.get('status') == 'preparing' else 200)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        return True
    if route.path == '/api/strategy/topics':
        from dynamic_registry import list_registry
        from dynamic_strategy import dynamic_projection
        from strategy_views import dataset
        db = sqlite3.connect(self.services.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            data = dataset(db)
            dynamic_projection(db, data['items'], data['morph'], data.get('revision'))
            result = list_registry(db)
            self.send_json(result, etag=str(result['version']))
        finally:
            db.close()
        return True
    if route.path == '/api/strategy/item' or (route.path == '/api/strategy' and params.get('view', [''])[0] in ('news', 'overview')):
        from strategy_views import read_view, read_item
        db = sqlite3.connect(self.services.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            if route.path == '/api/strategy/item':
                result = read_item(db, params.get('id', [''])[0])
            else:
                result = read_view(db, params, self.services.source_service.status())
            self.send_json(result if result is not None else {'error': '뉴스를 찾을 수 없습니다.'}, 200 if result is not None else 404)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    if route.path == '/api/strategy':
        from strategy import read_strategy
        db = self.services.connect(self.services.path)
        try:
            self.send_json(read_strategy(db, parse_qs(route.query), self.services.source_service.status()))
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    return False

def post(self, route, payload):
    is_topic = route.path == '/api/strategy/topics'
    topic_action = re.fullmatch('/api/strategy/topics/([^/]+)/(exclude|restore)', route.path)
    if is_topic or topic_action:
        from dynamic_registry import save_manual, set_excluded
        db = sqlite3.connect(self.services.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            if topic_action:
                topic_id, action = topic_action.groups()
                result = set_excluded(db, unquote(topic_id), action == 'exclude')
            else:
                result = save_manual(db, payload)
            self.send_json({'item': result} if result else {'error': '주제를 찾을 수 없습니다.'}, 200 if result else 404)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    return False
