"""Papers routes; shared transport checks stay in server_http."""
import re
import sqlite3
from urllib.parse import parse_qs, unquote

def get(self, route, params):
    status = 200
    paper_match = re.fullmatch('/api/papers/(.+)', route.path)
    if route.path == '/api/papers/pipeline':
        self.send_json(self.services.paper_pipeline.status())
        return True
    if route.path == '/api/papers/strategy':
        from paper_context import strategic_context
        with sqlite3.connect(self.services.path, timeout=10) as db:
            self.send_json(strategic_context(db))
        return True
    if route.path == '/api/papers' or paper_match:
        db = self.services.connect(self.services.path)
        try:
            if paper_match:
                item = self.services.paper(db, unquote(paper_match.group(1)))
                result = {'paper': item, 'analysis': self.services.paper_analysis_service.get(item['paper_id'], item=item)} if item else {'error': '논문을 찾을 수 없습니다.'}
                status = 200 if item else 404
            else:
                params = parse_qs(route.query)
                if 'window_days' in params:
                    params['window'] = params['window_days']
                result = self.services.read_papers(db, params)
                for item in result['items']:
                    item['analysis'] = self.services.paper_analysis_service.get(item['paper_id'], item=item)
                result['metadata_service'] = self.services.paper_service.status()
                result['pipeline'] = self.services.paper_pipeline.status()
                result['analysis_service'] = self.services.paper_analysis_service.status()
                from paper_graph import paper_coverage
                result['analysis_coverage'] = paper_coverage(db)
            self.send_json(result, status)
        except (ValueError, RuntimeError) as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    return False

def post(self, route, payload):
    is_paper_refresh = route.path in ('/api/papers/refresh', '/api/papers/pipeline')
    is_paper_analysis = route.path == '/api/papers/analyze'
    paper_action = re.fullmatch('/api/papers/(.+)/analyze', route.path)
    if is_paper_refresh or is_paper_analysis or paper_action:
        try:
            if route.path == '/api/papers/pipeline':
                self.send_json(self.services.paper_pipeline.configure(payload.get('enabled')), 200)
                return True
            ids = [unquote(paper_action.group(1))] if paper_action else payload.get('ids')
            if ids is not None and (not isinstance(ids, list) or not all((isinstance(i, str) for i in ids))):
                raise ValueError('논문 ID 목록 형식이 올바르지 않습니다.')
            params = parse_qs(route.query)
            if ids is None and any((params.get(k) for k in ('q', 'category', 'sector', 'analysis_status'))):
                db = self.services.connect(self.services.path)
                try:
                    ids = [item['paper_id'] for item in self.services.read_papers(db, dict(params, page=['1'], page_size=['50']))['items']]
                finally:
                    db.close()
            if is_paper_refresh:
                result = self.services.paper_service.refresh(ids, limit=int(payload.get('limit', 20)))
            else:
                result = self.services.paper_analysis_service.submit(ids[:10] if ids is not None else None, limit=1 if paper_action else int(payload.get('limit', 3)))
            self.send_json(result, 202)
        except (ValueError, RuntimeError) as error:
            self.send_json({'error': str(error)}, 409)
        return True
    return False
