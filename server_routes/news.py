"""News routes; shared transport checks stay in server_http."""
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs
from semantic import compare_mentions
from graph_rag import load_integrated_graph
from keyword_index import read_keyword_record
from url_archive import archived_rows

def get(self, route, params):
    status = 200
    if route.path.startswith('/api/graph/answers/'):
        try:
            result = self.services.question_service.get(route.path.rsplit('/', 1)[-1])
            self.send_json(result or {'error': '분석 작업을 찾을 수 없습니다.'}, 200 if result else 404)
        except (RuntimeError, sqlite3.OperationalError):
            self.send_json({'error': '근거 갱신 중입니다. 잠시 후 다시 조회해 주세요.'}, 503)
        return True
    if route.path == '/api/graph/integrated':
        db = sqlite3.connect(self.services.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            result = load_integrated_graph(db, parse_qs(route.query))
        except ValueError:
            result, status = ({'error': '그래프 필터를 확인해 주세요.'}, 400)
        finally:
            db.close()
        self.send_json(result, status)
        return True
    if route.path in {'/api/urls', '/api/urls/export.json'}:
        db = self.services.connect(self.services.path)
        try:
            params = parse_qs(route.query)
            result = archived_rows(db, params)
            if route.path.endswith('export.json'):
                params.update(page=['1'], page_size=['100'])
                result = archived_rows(db, params)
                items = list(result['items'])
                for page in range(2, result['total_pages'] + 1):
                    params['page'] = [str(page)]
                    items.extend(archived_rows(db, params)['items'])
                result = {'items': items, 'total': len(items)}
        except ValueError:
            result, status = ({'error': '보관함 날짜 또는 페이지를 확인해 주세요.'}, 400)
        finally:
            db.close()
        self.send_json(result, status)
        return True
    if route.path == '/api/keywords/document':
        db = self.services.connect(self.services.path)
        try:
            params = parse_qs(route.query)
            record_id = params.get('id', [''])[0]
            result = read_keyword_record(db, record_id)
            if result is None:
                result, status = ({'error': '키워드 기록을 찾을 수 없습니다.'}, 404)
        except ValueError:
            result, status = ({'error': '키워드 기록 ID를 확인해 주세요.'}, 400)
        finally:
            db.close()
        self.send_json(result, status)
        return True
    if route.path == '/api/briefing':
        db = self.services.connect(self.services.path)
        try:
            result = self.services.read_briefing(db, parse_qs(route.query))
        except ValueError:
            result, status = ({'error': '브리핑 날짜를 확인해 주세요.'}, 400)
        finally:
            db.close()
        self.send_json(result, status)
        return True
    if route.path == '/api/graph':
        db = self.services.connect(self.services.path)
        try:
            selected = self.services.graph_input(db, parse_qs(route.query))
            result = {'input': selected, 'analysis': self.services.graph_service.status(selected)}
        except ValueError:
            result, status = ({'error': '분석 범위와 날짜를 확인해 주세요.'}, 400)
        finally:
            db.close()
        self.send_json(result, status)
        return True
    if route.path == '/api/links' or re.fullmatch('/api/links/[0-9a-f]{24}', route.path):
        db = self.services.connect(self.services.path)
        try:
            if route.path == '/api/links':
                result = self.services.read_links(db, parse_qs(route.query), self.services.analysis_service)
            else:
                group = self.services.find_link_group(db, route.path.rsplit('/', 1)[1])
                if group is None:
                    result, status = ({'error': '링크 그룹을 찾을 수 없습니다.'}, 404)
                else:
                    result = dict(group, comparison=compare_mentions(group), analysis=self.services.analysis_service.status(group))
        except ValueError:
            result, status = ({'error': '날짜 또는 페이지 번호를 확인해 주세요.'}, 400)
        finally:
            db.close()
        self.send_json(result, status)
        return True
    return False

def post(self, route, payload):
    match = re.fullmatch('/api/links/([0-9a-f]{24})/analyze', route.path)
    is_graph = route.path == '/api/graph/analyze'
    is_question = route.path == '/api/graph/ask'
    if is_question:
        question = payload.get('question', '')
        ids = payload.get('node_ids', [])
        if not isinstance(question, str) or len(question.strip()) < 3 or len(question) > 2000 or (not isinstance(ids, list)) or (len(ids) > 20) or any((not isinstance(i, str) for i in ids)):
            self.send_json({'error': '질문과 선택 키워드를 확인해 주세요.'}, 400)
            return True
        try:
            answer = self.services.question_service.ask(question, ids, parse_qs(route.query))
            self.send_json(answer)
        except (RuntimeError, ValueError) as error:
            self.send_json({'error': str(error)}, 422)
        except sqlite3.OperationalError:
            self.send_json({'error': '근거 저장소가 갱신 중입니다. 잠시 후 다시 질문해 주세요.'}, 503)
        return True
    if match or is_graph:
        db = self.services.connect(self.services.path)
        try:
            group = self.services.graph_input(db, parse_qs(route.query)) if is_graph else self.services.find_link_group(db, match.group(1))
        except ValueError:
            self.send_json({'error': '분석 범위와 날짜를 확인해 주세요.'}, 400)
            return True
        finally:
            db.close()
        if group is None:
            self.send_json({'error': '링크 그룹을 찾을 수 없습니다.'}, 404)
            return True
        service = self.services.graph_service if is_graph else self.services.analysis_service
        if not group.get('mentions'):
            self.send_json({'error': '분석할 메시지가 없습니다.'}, 400)
            return True
        if not service.enabled:
            self.send_json({'error': '외부 의미 분석이 아직 활성화되지 않았습니다.'}, 403)
            return True
        try:
            status = service.submit(group)
            self.send_json({'status': status}, 200 if status == 'complete' else 202)
        except RuntimeError as error:
            self.send_json({'error': str(error)}, 429)
        return True
    return False
