"""Simulation routes; shared transport checks stay in server_http."""
import re
from mirofish_runtime import runtime_status

def get(self, route, params):
    status = 200
    simulation_match = re.fullmatch('/api/simulation/([a-zA-Z0-9_-]+)(/seed|/report)?', route.path)
    if route.path == '/api/simulation' or simulation_match:
        try:
            if not simulation_match:
                result = {'runtime': runtime_status(), 'runs': self.services.simulation_service.list_runs()}
            else:
                run_id, resource = simulation_match.groups()
                run = self.services.simulation_service.get_run(run_id)
                if run is None:
                    result, status = ({'error': '시뮬레이션을 찾을 수 없습니다.'}, 404)
                elif resource == '/seed':
                    result = self.services.simulation_service.seed(run_id)
                elif resource == '/report':
                    result = self.services.simulation_service.report(run_id)
                else:
                    result = {'run': run}
        except (RuntimeError, ValueError, OSError):
            result, status = ({'error': '시뮬레이션 기록을 불러오지 못했습니다.'}, 400)
        self.send_json(result, status)
        return True
    return False

def post(self, route, payload):
    is_simulation = route.path == '/api/simulation'
    is_runtime_start = route.path == '/api/simulation/runtime/start'
    simulation_action = re.fullmatch('/api/simulation/([a-zA-Z0-9_-]+)/(start|stop|resume|interview|chat)', route.path)
    if is_runtime_start:
        try:
            from mirofish_runtime import start_runtime
            start_runtime()
            self.send_json({'runtime': runtime_status()})
        except (RuntimeError, ValueError, OSError):
            self.send_json({'error': '엔진을 시작하지 못했습니다. 설치 상태와 .env.mirofish 설정을 확인해 주세요.', 'runtime': runtime_status()}, 409)
        return True
    if is_simulation or simulation_action:
        try:
            if is_simulation:
                db = self.services.connect(self.services.path)
                try:
                    items = self.services.simulation_news(db, payload, prepared=True)
                finally:
                    db.close()
                result = self.services.simulation_service.create_run(items, payload)
            else:
                run_id, action = simulation_action.groups()
                if self.services.simulation_service.get_run(run_id) is None:
                    self.send_json({'error': '시뮬레이션을 찾을 수 없습니다.'}, 404)
                    return True
                if action == 'interview':
                    result = self.services.simulation_service.interview(run_id, payload.get('agent_id'), payload.get('prompt', ''), payload.get('platform'))
                    self.send_json({'result': result})
                    return True
                if action == 'chat':
                    result = self.services.simulation_service.report_chat(run_id, payload.get('message', ''), payload.get('chat_history', []))
                    self.send_json({'result': result})
                    return True
                result = getattr(self.services.simulation_service, action)(run_id)
            self.send_json({'run': result}, 202)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        except RuntimeError as error:
            self.send_json({'error': str(error)}, 409)
        return True
    return False
