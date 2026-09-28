"""Intelligence routes; shared transport checks stay in server_http."""
import re
import sqlite3

def get(self, route, params):
    if route.path == '/api/intelligence':
        try:
            result = self.services.intelligence_service.get(params.get('view', ['overview'])[0], params)
            missing = 'item' in result and result['item'] is None
            self.send_json(result if not missing else {'error': '항목을 찾을 수 없습니다.'}, 404 if missing else 200)
        except (ValueError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except sqlite3.OperationalError:
            self.send_json({'error': '전략 자료를 준비하고 있습니다. 잠시 후 다시 조회해 주세요.'}, 503)
        return True
    return False

def post(self, route, payload):
    intelligence_action = re.fullmatch('/api/intelligence/(events|concepts|decisions|scenarios|profiles|experiments|research|query)', route.path)
    if intelligence_action:
        try:
            result = self.services.intelligence_service.mutate(intelligence_action.group(1), payload)
            self.send_json(result, 202 if result.get('run') else 200)
        except (ValueError, TypeError) as error:
            self.send_json({'error': str(error)}, 400)
        except RuntimeError as error:
            self.send_json({'error': str(error)}, 409)
        return True
    return False
