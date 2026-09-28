"""Wiki routes; shared transport checks stay in server_http."""
import json
import sqlite3

def get(self, route, params):
    if route.path == '/api/wiki/network':
        try:
            self.send_json(self.services.wiki_service.network(identity=params.get('id', [''])[0][:200], query=params.get('q', [''])[0][:200], layer=params.get('layer', ['all'])[0], limit=params.get('limit', ['100'])[0], source_url=params.get('source_url', [''])[0][:2048], paper_id=params.get('paper_id', [''])[0][:100]))
        except (ValueError, TypeError):
            self.send_json({'error': '지식 연결 조회 조건을 확인하세요.'}, 400)
        except sqlite3.OperationalError:
            self.send_json({'error': '저장소 갱신 중입니다.'}, 503)
        return True
    if route.path in ('/api/wiki/export', '/api/wiki/vault'):
        try:
            vault = route.path.endswith('/vault')
            content = self.services.wiki_service.vault() if vault else self.services.wiki_service.export().encode('utf-8')
        except sqlite3.OperationalError:
            self.send_json({'error': '저장소가 갱신 중입니다.'}, 503)
            return True
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip' if vault else 'text/markdown; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="news-wiki.' + ('zip' if vault else 'md') + '"')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)
        return True
    if route.path == '/api/wiki':
        try:
            self.send_json(self.services.wiki_service.get(params.get('id', [None])[0], params.get('q', [''])[0][:200], params.get('source_url', [''])[0][:2048]))
        except sqlite3.OperationalError:
            self.send_json({'error': '위키 저장소가 갱신 중입니다. 잠시 후 다시 조회해 주세요.'}, 503)
        return True
    if route.path in ('/wiki', '/wiki.js', '/wiki.css', '/knowledge', '/wiki-network.js', '/wiki-network-3d.js', '/wiki-network.css', '/three.module.js', '/three.core.js'):
        name = 'wiki.html' if route.path == '/wiki' else 'wiki-network.html' if route.path == '/knowledge' else route.path[1:]
        content = (self.services.ROOT / 'static' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css'}[name.rsplit('.', 1)[-1]] + '; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)
        return True
    return False

def post(self, route, payload):
    if route.path == '/api/wiki':
        try:
            action = payload.get('action', 'refresh')
            if action == 'configure':
                result = self.services.wiki_service.manage(payload)
            elif action == 'alias':
                result = self.services.wiki_service.alias(payload)
            elif action == 'refresh':
                result = self.services.wiki_service.request(payload.get('topic', ''))
            elif action == 'archive_question':
                job_id = payload.get('job_id')
                if not isinstance(job_id, str):
                    raise ValueError('질문 작업 ID가 필요합니다.')
                job = self.services.question_service.get(job_id)
                if not job or job['status'] != 'complete':
                    raise ValueError('현재 검토 완료된 질문만 저장할 수 있습니다.')
                with self.services.question_service.db() as db:
                    row = db.execute('SELECT request_json FROM graph_question_jobs WHERE id=?', (job_id,)).fetchone()
                result = self.services.wiki_service.archive_question(job_id, json.loads(row[0])['question'], job['result'])
            else:
                raise ValueError('지원하지 않는 위키 작업입니다.')
            self.send_json(result, 202)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        except sqlite3.OperationalError:
            self.send_json({'error': '저장소가 갱신 중입니다. 다시 요청해 주세요.'}, 503)
        return True
    return False
