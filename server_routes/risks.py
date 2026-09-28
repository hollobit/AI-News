"""Risks routes; shared transport checks stay in server_http."""
import re
import sqlite3
from urllib.parse import parse_qs, unquote

def get(self, route, params):
    risk_match = re.fullmatch('/api/risks/([^/]+)', route.path)
    if route.path == '/api/risks/graph':
        from risk_graph import load_risk_graph
        db = sqlite3.connect(self.services.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            self.send_json(load_risk_graph(db, parse_qs(route.query)))
        except (ValueError, RuntimeError) as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    if risk_match or (route.path == '/api/risks' and params.get('view', [''])[0] == 'page'):
        from risk_views import read_risk_page, read_risk_detail
        db = sqlite3.connect(self.services.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            result = read_risk_detail(db, unquote(risk_match.group(1))) if risk_match else read_risk_page(db, params)
            self.send_json(result if result else {'error': '위험 평가를 찾을 수 없습니다.'}, 200 if result else 404)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    if route.path == '/api/risks':
        from risk_analysis import read_risks
        db = self.services.connect(self.services.path)
        try:
            self.send_json(read_risks(db, params=parse_qs(route.query)))
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        finally:
            db.close()
        return True
    return False
