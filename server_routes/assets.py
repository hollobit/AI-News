"""Assets routes; shared transport checks stay in server_http."""
import json
from urllib.parse import parse_qs
from news_repository import read_news

def get(self, route, params):
    status = 200
    if route.path == '/api/news':
        db = self.services.connect(self.services.path)
        try:
            result = read_news(db, parse_qs(route.query))
        except ValueError:
            status, result = (400, {'error': '올바른 날짜를 선택해 주세요.'})
        finally:
            db.close()
        content = json.dumps(result, ensure_ascii=False).encode()
        mime = 'application/json; charset=utf-8'
    elif route.path in {'/pipeline-health.js', '/pipeline-health.css', '/research-layout.css', '/workspace-navigation.js', '/workspace-ui.css', '/workspace.js', '/workspace.css', '/public-data.js', '/observatory-search.js', '/observatory.js', '/observatory.css', '/strategy.js', '/strategy-evidence.js', '/strategy-workflow.js', '/strategy-improvement.js', '/strategy-baseline.js', '/strategy-topics.js', '/strategy-risks.js', '/strategy.css', '/news-network.js', '/news-network.css', '/papers.js', '/papers.css', '/risks.js', '/risks.css', '/risk-network.js', '/risk-network.css', '/intelligence.js', '/intelligence.css'}:
        content = (self.services.ROOT / 'static' / route.path[1:]).read_bytes()
        if route.path == '/risks.js':
            content += b'\n' + (self.services.ROOT / 'static/risk-network-bootstrap.js').read_bytes()
        mime = 'application/javascript; charset=utf-8' if route.path.endswith('.js') else 'text/css; charset=utf-8'
    elif route.path in {'/graph', '/graph.js', '/archive', '/research', '/research.js', '/simulation', '/simulation.js'}:
        name = {'/graph': 'graph.html', '/graph.js': 'graph.js', '/archive': 'archive.html', '/research': 'research.html', '/research.js': 'research.js', '/simulation': 'simulation.html', '/simulation.js': 'simulation.js'}[route.path]
        content = (self.services.ROOT / 'static' / name).read_bytes()
        mime = 'application/javascript; charset=utf-8' if name.endswith('.js') else 'text/html; charset=utf-8'
    elif route.path in {'/intelligence', '/intelligence/events', '/intelligence/topics', '/intelligence/decisions', '/intelligence/scenarios', '/intelligence/research', '/intelligence/operations', '/intelligence/concepts', '/intelligence/experiments', '/intelligence/profiles', '/intelligence/risk_history'}:
        content = (self.services.ROOT / 'static/intelligence.html').read_bytes()
        mime = 'text/html; charset=utf-8'
    elif route.path == '/observatory':
        content = (self.services.ROOT / 'static/observatory.html').read_bytes()
        mime = 'text/html; charset=utf-8'
    elif route.path == '/risks':
        content = (self.services.ROOT / 'static/risks.html').read_bytes()
        mime = 'text/html; charset=utf-8'
    elif route.path == '/papers':
        content = (self.services.ROOT / 'static/papers.html').read_bytes()
        mime = 'text/html; charset=utf-8'
    elif route.path == '/mirofish-license':
        content = (self.services.ROOT / 'vendor/mirofish/LICENSE').read_bytes()
        mime = 'text/plain; charset=utf-8'
    elif route.path == '/mirofish-source.zip':
        content, mime = (self.services.source_bundle(), 'application/zip')
    elif route.path == '/news' or (route.path == '/' and route.query):
        content = (self.services.ROOT / 'static/index.html').read_bytes()
        mime = 'text/html; charset=utf-8'
    elif route.path in {'/', '/strategy', '/operations'}:
        content = (self.services.ROOT / 'static/strategy.html').read_bytes()
        mime = 'text/html; charset=utf-8'
    else:
        status, content, mime = (404, b'Not found', 'text/plain')
    self.send_response(status)
    self.send_header('Content-Type', mime)
    self.send_header('Content-Length', str(len(content)))
    self.send_header('Cache-Control', 'no-store')
    self.send_header('X-Content-Type-Options', 'nosniff')
    self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
    self.end_headers()
    self.wfile.write(content)
    return True
