"""Research routes; shared transport checks stay in server_http."""
import re
from urllib.parse import parse_qs
from url_archive import archived_rows
from news_repository import joined_articles

def get(self, route, params):
    status = 200
    research_match = re.fullmatch('/api/research/([a-zA-Z0-9_-]+)(/documents)?', route.path)
    if route.path == '/api/research' or research_match:
        try:
            if not research_match:
                result = {'enabled': self.services.research_service.enabled, 'runs': self.services.research_service.list_runs()}
            else:
                run_id = research_match.group(1)
                result = self.services.research_service.get_run(run_id)
                if result is None:
                    result, status = ({'error': '분석 스냅샷을 찾을 수 없습니다.'}, 404)
                elif research_match.group(2):
                    params = parse_qs(route.query)
                    result = self.services.research_service.documents(run_id, page=int(params.get('page', ['1'])[0]), page_size=min(100, int(params.get('page_size', ['24'])[0])))
        except ValueError:
            result, status = ({'error': '분석 범위 또는 페이지를 확인해 주세요.'}, 400)
        self.send_json(result, status)
        return True
    return False

def post(self, route, payload):
    is_research = route.path == '/api/research'
    resume_match = re.fullmatch('/api/research/([a-zA-Z0-9_-]+)/resume', route.path)
    resume_match = re.fullmatch('/api/research/([a-zA-Z0-9_-]+)/resume', route.path)
    if is_research or resume_match:
        if not self.services.research_service.enabled:
            self.send_json({'error': '외부 분석이 비활성화되어 있습니다.'}, 403)
            return True
        try:
            if resume_match:
                result = self.services.research_service.resume(resume_match.group(1))
            else:
                db = self.services.connect(self.services.path)
                try:
                    db.execute('BEGIN')
                    rows = [dict(row) for row in joined_articles(db)]
                    rows.extend(self.services.unindexed_link_rows(db, rows))
                    rows.extend(self.services.hidden_link_rows(db, rows))
                    records = archived_rows(db, {'active': ['1'], 'page_size': ['100']})
                    archive = list(records['items'])
                    for page in range(2, records['total_pages'] + 1):
                        archive.extend(archived_rows(db, {'active': ['1'], 'page_size': ['100'], 'page': [str(page)]})['items'])
                finally:
                    db.close()
                if payload.get('sample_limit') is not None:
                    limit = int(payload['sample_limit'])
                    if not 1 <= limit <= 100:
                        raise ValueError('선택 분석은 1~100개 뉴스로 제한됩니다.')
                    db = self.services.connect(self.services.path)
                    try:
                        from strategy import select_strategy_items
                        params = parse_qs(route.query)
                        rows = select_strategy_items(db, params)[:limit]
                    finally:
                        db.close()
                    archive = []
                result = self.services.research_service.create_run(rows, archive)
            self.send_json({'run': result} if result else {'error': '분석 스냅샷을 찾을 수 없습니다.'}, 202 if result else 404)
        except (RuntimeError, ValueError) as error:
            self.send_json({'error': str(error)}, 409)
        return True
    return False
