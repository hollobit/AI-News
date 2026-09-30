"""HTTP routes over explicitly supplied services and query functions."""
from server_context import ServerContext
from server_routes import GET_ROUTES, POST_ROUTES
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from projection_cache import read_projections

def make_handler(context):
    if isinstance(context, dict):
        context = ServerContext(**context)

    class Handler(BaseHTTPRequestHandler):
        services = context

        def send_json(self, result, status=200, *, etag=None):
            if etag is not None and status == 200:
                tag = '"' + str(etag).strip('"') + '"'
                matches = [value.strip().removeprefix('W/') for value in self.headers.get('If-None-Match', '').split(',')]
                if tag in matches or '*' in matches:
                    self.send_response(304)
                    self.send_header('ETag', tag)
                    self.send_header('Cache-Control', 'private, no-cache')
                    self.end_headers()
                    return
            content = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'private, no-cache' if etag is not None else 'no-store')
            if etag is not None:
                self.send_header('ETag', '"' + str(etag).strip('"') + '"')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            try:
                with read_projections():
                    self._get()
            except sqlite3.OperationalError:
                self.send_json({'error': '자료를 준비하고 있습니다. 잠시 후 다시 조회해 주세요.'}, 503)

        def _get(self):
            route = urlparse(self.path)
            params = parse_qs(route.query)
            for route_handler in GET_ROUTES:
                if route_handler(self, route, params):
                    return

        def do_POST(self):
            route = urlparse(self.path)
            intelligence_action = re.fullmatch('/api/intelligence/(events|concepts|decisions|scenarios|profiles|experiments|research|query)', route.path)
            match = re.fullmatch('/api/links/([0-9a-f]{24})/analyze', route.path)
            incident_ack = re.fullmatch('/api/operations/incidents/([0-9]+)/ack', route.path)
            is_graph = route.path == '/api/graph/analyze'
            is_question = route.path == '/api/graph/ask'
            is_research = route.path == '/api/research'
            is_simulation = route.path == '/api/simulation'
            is_reach = route.path in ('/api/wiki', '/api/article-explanations', '/api/agent-reach/read', '/api/agent-reach/repair', '/api/agent-reach/subscriptions', '/api/agent-reach/subscription-toggle')
            is_source = route.path == '/api/sources'
            is_workflow = route.path == '/api/workflows'
            is_improvement = route.path == '/api/improvement'
            is_topic = route.path == '/api/strategy/topics'
            topic_action = re.fullmatch('/api/strategy/topics/([^/]+)/(exclude|restore)', route.path)
            is_baseline = route.path == '/api/baseline'
            baseline_action = re.fullmatch('/api/baseline/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
            is_paper_refresh = route.path in ('/api/papers/refresh', '/api/papers/pipeline')
            is_paper_analysis = route.path == '/api/papers/analyze'
            paper_action = re.fullmatch('/api/papers/(.+)/analyze', route.path)
            improvement_action = re.fullmatch('/api/improvement/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
            workflow_resume = re.fullmatch('/api/workflows/([a-zA-Z0-9_-]+)/resume', route.path)
            is_runtime_start = route.path == '/api/simulation/runtime/start'
            simulation_action = re.fullmatch('/api/simulation/([a-zA-Z0-9_-]+)/(start|stop|resume|interview|chat)', route.path)
            resume_match = re.fullmatch('/api/research/([a-zA-Z0-9_-]+)/resume', route.path)
            if not incident_ack and not is_reach and (not intelligence_action) and (not match) and (not is_graph) and (not is_question) and (not is_research) and (not resume_match) and (not is_simulation) and (not simulation_action) and (not is_runtime_start) and (not is_source) and (not is_workflow) and (not workflow_resume) and (not is_improvement) and (not improvement_action) and (not is_paper_refresh) and (not is_paper_analysis) and (not paper_action) and (not is_baseline) and (not baseline_action) and (not is_topic) and (not topic_action):
                self.send_json({'error': 'Not found'}, 404)
                return
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if host not in {f'127.0.0.1:{self.services.port}', f'localhost:{self.services.port}'} or (origin and origin != f'http://{host}') or self.headers.get_content_type() != 'application/json':
                self.send_json({'error': '뉴스 페이지에서 분석을 요청해 주세요.'}, 403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > (32768 if is_reach or intelligence_action or is_topic or topic_action or is_question or is_simulation or simulation_action or is_workflow or is_improvement or is_paper_refresh or is_paper_analysis or paper_action else 1024):
                    raise ValueError
                payload = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({'error': '분석 요청 형식이 올바르지 않습니다.'}, 400)
                return
            for route_handler in POST_ROUTES:
                if route_handler(self, route, payload):
                    return
    return Handler
