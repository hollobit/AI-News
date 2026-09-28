"""HTTP routes over explicitly supplied services and query functions."""
import json
import re
import sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, unquote
from projection_cache import read_projections
from semantic import compare_mentions
from graph_rag import load_integrated_graph
from keyword_index import read_keyword_record
from url_archive import archived_rows
from mirofish_runtime import runtime_status
from news_repository import joined_articles, read_news


def make_handler(context):
    analysis_service = context['analysis_service']
    article_service = context['article_service']
    baseline_service = context['baseline_service']
    connect = context['connect']
    corpus_status = context['corpus_status']
    graph_service = context['graph_service']
    improvement_catalog = context['improvement_catalog']
    improvement_service = context['improvement_service']
    intelligence_service = context['intelligence_service']
    observatory_service = context['observatory_service']
    paper = context['paper']
    paper_analysis_service = context['paper_analysis_service']
    paper_pipeline = context['paper_pipeline']
    paper_service = context['paper_service']
    path = context['path']
    port = context['port']
    public_improvement = context['public_improvement']
    question_service = context['question_service']
    reach_pipeline = context['reach_pipeline']
    reach_service = context['reach_service']
    read_papers = context['read_papers']
    research_service = context['research_service']
    simulation_service = context['simulation_service']
    source_service = context['source_service']
    wiki_service = context['wiki_service']
    workflow_service = context['workflow_service']
    ROOT = context['ROOT']
    configured_channels = context['configured_channels']
    find_link_group = context['find_link_group']
    graph_input = context['graph_input']
    hidden_link_rows = context['hidden_link_rows']
    read_briefing = context['read_briefing']
    read_links = context['read_links']
    simulation_news = context['simulation_news']
    source_bundle = context['source_bundle']
    unindexed_link_rows = context['unindexed_link_rows']

    class Handler(BaseHTTPRequestHandler):
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
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "private, no-cache" if etag is not None else "no-store")
            if etag is not None:
                self.send_header('ETag', '"' + str(etag).strip('"') + '"')
            self.send_header("X-Content-Type-Options", "nosniff")
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
            status = 200
            params = parse_qs(route.query)
            if route.path == '/api/wiki/network':
                try:self.send_json(wiki_service.network(identity=params.get('id',[''])[0][:200],query=params.get('q',[''])[0][:200],layer=params.get('layer',['all'])[0],limit=params.get('limit',['100'])[0],source_url=params.get('source_url',[''])[0][:2048],paper_id=params.get('paper_id',[''])[0][:100]))
                except (ValueError,TypeError):self.send_json({'error':'지식 연결 조회 조건을 확인하세요.'},400)
                except sqlite3.OperationalError:self.send_json({'error':'저장소 갱신 중입니다.'},503)
                return
            if route.path in ('/api/wiki/export','/api/wiki/vault'):
                try:
                    vault = route.path.endswith('/vault')
                    content = wiki_service.vault() if vault else wiki_service.export().encode('utf-8')
                except sqlite3.OperationalError:
                    self.send_json({'error': '저장소가 갱신 중입니다.'}, 503)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip' if vault else 'text/markdown; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="news-wiki.'+('zip' if vault else 'md')+'"')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if route.path == '/api/wiki':
                try:
                    self.send_json(wiki_service.get(params.get('id', [None])[0], params.get('q', [''])[0][:200], params.get('source_url', [''])[0][:2048]))
                except sqlite3.OperationalError:
                    self.send_json({'error': '위키 저장소가 갱신 중입니다. 잠시 후 다시 조회해 주세요.'}, 503)
                return
            if route.path in ('/wiki', '/wiki.js', '/wiki.css','/knowledge','/wiki-network.js','/wiki-network-3d.js','/wiki-network.css','/three.module.js','/three.core.js'):
                name = 'wiki.html' if route.path == '/wiki' else 'wiki-network.html' if route.path=='/knowledge' else route.path[1:]
                content = (ROOT / 'static' / name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', {'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css'}[name.rsplit('.',1)[-1]] + '; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(content)
                return
            if route.path == '/paper-context.js':
                content=(ROOT/'static'/'paper-context.js').read_bytes()
                self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            if route.path == '/api/article-explanations':
                try:self.send_json(article_service.get(params.get('url',[''])[0]))
                except ValueError as error:self.send_json({'error':str(error)},400)
                return
            if route.path in ('/article','/article.js'):
                name='article.html' if route.path=='/article' else 'article.js'
                content=(ROOT/'static'/name).read_bytes()
                self.send_response(200);self.send_header('Content-Type',('text/html' if name.endswith('html') else 'application/javascript')+'; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            if route.path in ('/sources','/sources.js','/sources.css'):
                name='sources.html' if route.path=='/sources' else route.path[1:]
                content=(ROOT/'static'/name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type',{'html':'text/html','js':'application/javascript','css':'text/css'}[name.rsplit('.',1)[1]]+'; charset=utf-8')
                self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            if route.path == '/api/agent-reach':
                self.send_json(reach_service.status()); return
            if route.path == '/api/corpus/status':
                try:self.send_json(corpus_status.get())
                except sqlite3.OperationalError:self.send_json({'error':'수집량 집계 중입니다.'},503)
                return
            if route.path == '/api/agent-reach/pipeline':
                self.send_json(reach_pipeline.status());return
            if route.path == '/api/agent-reach/passages':
                from source_store import search
                from link_groups import canonical_url
                query=params.get('q',[''])[0][:500];url=canonical_url(params.get('url',[''])[0])
                with sqlite3.connect(path,timeout=2) as db:self.send_json({'evidence':search(db,query,urls=[url])})
                return
            if route.path == '/api/agent-reach/source':
                from source_store import detail
                with sqlite3.connect(path) as db:self.send_json(detail(db,params.get('url',[''])[0]))
                return
            if route.path.startswith('/api/agent-reach/tasks/'):
                result=reach_pipeline.get(route.path.rsplit('/',1)[-1]);self.send_json(result or {'error':'작업 없음'},200 if result else 404);return
            if route.path.startswith('/api/agent-reach/jobs/'):
                result=reach_service.get(route.path.rsplit('/',1)[-1])
                self.send_json(result or {'error':'읽기 작업을 찾을 수 없습니다.'},200 if result else 404); return
            if route.path.startswith('/api/graph/answers/'):
                try:
                    result=question_service.get(route.path.rsplit('/',1)[-1])
                    self.send_json(result or {'error':'분석 작업을 찾을 수 없습니다.'},200 if result else 404)
                except (RuntimeError,sqlite3.OperationalError):
                    self.send_json({'error':'근거 갱신 중입니다. 잠시 후 다시 조회해 주세요.'},503)
                return
            if route.path=='/api/collector/channels':
                from collector_status import channel_status
                with sqlite3.connect(path,timeout=.25) as db:
                    db.row_factory=sqlite3.Row
                    self.send_json(channel_status(db,configured_channels(),ROOT/'config.json'))
                return
            if route.path == '/api/intelligence':
                try:
                    result = intelligence_service.get(params.get('view',['overview'])[0],params)
                    missing = 'item' in result and result['item'] is None
                    self.send_json(result if not missing else {'error':'항목을 찾을 수 없습니다.'},404 if missing else 200)
                except (ValueError,TypeError) as error:
                    self.send_json({'error':str(error)},400)
                except sqlite3.OperationalError:
                    self.send_json({'error':'전략 자료를 준비하고 있습니다. 잠시 후 다시 조회해 주세요.'},503)
                return
            if route.path == '/api/observatory/events':
                # Short streams bound per-client resources. EventSource reconnects
                # with Last-Event-ID; only changed persisted records are sent.
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    self.send_json({'error':'동일 사이트에서만 연결할 수 있습니다.'},403);return
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Connection','close');self.end_headers()
                import time
                last=self.headers.get('Last-Event-ID','')
                try:
                    for _ in range(10):
                        try:
                            payload=observatory_service.events()
                            if payload['version']!=last:
                                last=payload['version']
                                message='id: '+last+'\nevent: processing\ndata: '+json.dumps(payload,ensure_ascii=False)+'\n\n'
                            else:message=': heartbeat\n\n'
                        except sqlite3.OperationalError:message=': database-busy\n\n'
                        self.wfile.write(message.encode());self.wfile.flush();time.sleep(2)
                except (BrokenPipeError,ConnectionResetError):pass
                self.close_connection=True;return
            if route.path == '/api/observatory/status':
                self.send_json(observatory_service.status());return
            if route.path == '/api/observatory/history':
                try:self.send_json(observatory_service.events())
                except sqlite3.OperationalError:self.send_json({'error':'처리 기록 갱신 중입니다.'},503)
                return
            if route.path == '/api/observatory':
                try:
                    expanded=params.get('expand',['0'])[0].lower() in {'1','true','yes','expanded'}
                    result=observatory_service.request(int(params.get('window',['14'])[0]),expanded)
                    self.send_json(result,202 if result.get('status')=='preparing' else 200)
                except ValueError as error:self.send_json({'error':str(error)},400)
                return
            if route.path == '/api/strategy/topics':
                from dynamic_registry import list_registry
                from dynamic_strategy import dynamic_projection
                from strategy_views import dataset
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    data = dataset(db)
                    dynamic_projection(db, data['items'], data['morph'], data.get('revision'))
                    result = list_registry(db)
                    self.send_json(result, etag=str(result['version']))
                finally:
                    db.close()
                return
            if route.path == '/api/runtime/automation':
                from automation_runtime import runtime_status as automation_status
                self.send_json(automation_status())
                return
            if route.path == '/api/runtime/views':
                from projection_cache import cache_info
                self.send_json(cache_info())
                return
            if route.path == '/api/runtime/analysis':
                from llm_runtime import runtime_status as analysis_runtime_status
                self.send_json(analysis_runtime_status())
                return
            compact_match = re.fullmatch(r'/api/(baseline|improvement|workflows)(?:/([a-zA-Z0-9_-]+))?', route.path)
            if compact_match and params.get('view', [''])[0] == 'status':
                from status_views import status_response
                kind, run_id = compact_match.groups()
                db = sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True, timeout=15)
                try:
                    active_workers = baseline_service.worker_counts()
                    result = status_response(db, kind, run_id,
                        enabled=baseline_service.enabled if kind == 'baseline' else workflow_service.enabled,
                        active_workers=active_workers)
                    self.send_json(result if result is not None else {'error': '실행을 찾을 수 없습니다.'},
                                   200 if result is not None else 404, etag=result.get('version') if result else None)
                finally:
                    db.close()
                return
            if route.path == '/api/strategy/item' or (route.path == '/api/strategy' and params.get('view', [''])[0] in ('news', 'overview')):
                from strategy_views import read_view, read_item
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    if route.path == '/api/strategy/item':
                        result = read_item(db, params.get('id', [''])[0])
                    else:
                        result = read_view(db, params, source_service.status())
                    self.send_json(result if result is not None else {'error': '뉴스를 찾을 수 없습니다.'},
                                   200 if result is not None else 404)
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            baseline_match = re.fullmatch(r'/api/baseline/([a-zA-Z0-9_-]+)', route.path)
            if route.path == '/api/baseline' or baseline_match:
                if baseline_match:
                    result = baseline_service.get(baseline_match.group(1))
                    self.send_json(result or {'error': '기본 분석 실행을 찾을 수 없습니다.'}, 200 if result else 404)
                else:
                    self.send_json({'runs': baseline_service.list(), 'enabled': baseline_service.enabled})
                return
            if route.path == '/api/papers/pipeline':
                self.send_json(paper_pipeline.status());return
            if route.path == '/api/papers/strategy':
                from paper_context import strategic_context
                with sqlite3.connect(path,timeout=10) as db:self.send_json(strategic_context(db))
                return
            paper_match = re.fullmatch(r'/api/papers/(.+)', route.path)
            if route.path == '/api/papers' or paper_match:
                db = connect(path)
                try:
                    if paper_match:
                        item = paper(db, unquote(paper_match.group(1)))
                        result = {'paper': item, 'analysis': paper_analysis_service.get(item['paper_id'], item=item)} if item else {'error': '논문을 찾을 수 없습니다.'}
                        status = 200 if item else 404
                    else:
                        params = parse_qs(route.query)
                        if 'window_days' in params: params['window'] = params['window_days']
                        result = read_papers(db, params)
                        for item in result['items']:
                            item['analysis'] = paper_analysis_service.get(item['paper_id'], item=item)
                        result['metadata_service'] = paper_service.status()
                        result['pipeline'] = paper_pipeline.status()
                        result['analysis_service'] = paper_analysis_service.status()
                        from paper_graph import paper_coverage
                        result['analysis_coverage'] = paper_coverage(db)
                    self.send_json(result, status)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            improvement_match = re.fullmatch(r'/api/improvement/([a-zA-Z0-9_-]+)', route.path)
            if route.path == '/api/improvement' or improvement_match:
                if improvement_match:
                    result = public_improvement(improvement_service.get(improvement_match.group(1)))
                    if result:
                        result['catalog'] = improvement_catalog()
                    self.send_json(result or {'error': '자기개선 순환을 찾을 수 없습니다.'}, 200 if result else 404)
                else:
                    self.send_json({'runs': [public_improvement(run) for run in improvement_service.list()], 'enabled': workflow_service.enabled,
                                    'catalog': improvement_catalog()})
                return
            if route.path == '/api/risks/graph':
                from risk_graph import load_risk_graph
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    self.send_json(load_risk_graph(db, parse_qs(route.query)))
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            risk_match = re.fullmatch(r'/api/risks/([^/]+)', route.path)
            if risk_match or (route.path == '/api/risks' and params.get('view', [''])[0] == 'page'):
                from risk_views import read_risk_page, read_risk_detail
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    result = read_risk_detail(db, unquote(risk_match.group(1))) if risk_match else read_risk_page(db, params)
                    self.send_json(result if result else {'error':'위험 평가를 찾을 수 없습니다.'}, 200 if result else 404)
                except ValueError as error:
                    self.send_json({'error':str(error)},400)
                finally:
                    db.close()
                return
            if route.path == '/api/risks':
                from risk_analysis import read_risks
                db = connect(path)
                try:
                    self.send_json(read_risks(db, params=parse_qs(route.query)))
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            if route.path == '/api/sources':
                self.send_json(source_service.status())
                return
            if route.path == '/api/strategy':
                from strategy import read_strategy
                db = connect(path)
                try:
                    self.send_json(read_strategy(db, parse_qs(route.query), source_service.status()))
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            workflow_match = re.fullmatch(r'/api/workflows/([a-zA-Z0-9_-]+)', route.path)
            if route.path == '/api/workflows' or workflow_match:
                if workflow_match:
                    result = workflow_service.get_run(workflow_match.group(1))
                    self.send_json(result or {'error': '분석 사이클을 찾을 수 없습니다.'}, 200 if result else 404)
                else:
                    self.send_json({'enabled': workflow_service.enabled, 'runs': workflow_service.list_runs()})
                return
            simulation_match = re.fullmatch(r"/api/simulation/([a-zA-Z0-9_-]+)(/seed|/report)?", route.path)
            if route.path == "/api/simulation" or simulation_match:
                try:
                    if not simulation_match:
                        result = {"runtime": runtime_status(), "runs": simulation_service.list_runs()}
                    else:
                        run_id, resource = simulation_match.groups()
                        run = simulation_service.get_run(run_id)
                        if run is None:
                            result, status = {"error": "시뮬레이션을 찾을 수 없습니다."}, 404
                        elif resource == "/seed":
                            result = simulation_service.seed(run_id)
                        elif resource == "/report":
                            result = simulation_service.report(run_id)
                        else:
                            result = {"run": run}
                except (RuntimeError, ValueError, OSError):
                    result, status = {"error": "시뮬레이션 기록을 불러오지 못했습니다."}, 400
                self.send_json(result, status)
                return
            research_match = re.fullmatch(r"/api/research/([a-zA-Z0-9_-]+)(/documents)?", route.path)
            if route.path == "/api/research" or research_match:
                try:
                    if not research_match:
                        result = {"enabled": research_service.enabled, "runs": research_service.list_runs()}
                    else:
                        run_id = research_match.group(1)
                        result = research_service.get_run(run_id)
                        if result is None:
                            result, status = {"error": "분석 스냅샷을 찾을 수 없습니다."}, 404
                        elif research_match.group(2):
                            params = parse_qs(route.query)
                            result = research_service.documents(run_id, page=int(params.get("page", ["1"])[0]),
                                                                page_size=min(100, int(params.get("page_size", ["24"])[0])))
                except ValueError:
                    result, status = {"error": "분석 범위 또는 페이지를 확인해 주세요."}, 400
                self.send_json(result, status)
                return
            if route.path == "/api/graph/integrated":
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    result = load_integrated_graph(db, parse_qs(route.query))
                except ValueError:
                    result, status = {"error": "그래프 필터를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path in {"/api/urls", "/api/urls/export.json"}:
                db = connect(path)
                try:
                    params = parse_qs(route.query)
                    result = archived_rows(db, params)
                    if route.path.endswith("export.json"):
                        params.update(page=["1"], page_size=["100"])
                        result = archived_rows(db, params)
                        items = list(result["items"])
                        for page in range(2, result["total_pages"] + 1):
                            params["page"] = [str(page)]
                            items.extend(archived_rows(db, params)["items"])
                        result = {"items": items, "total": len(items)}
                except ValueError:
                    result, status = {"error": "보관함 날짜 또는 페이지를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/keywords/document":
                db = connect(path)
                try:
                    params = parse_qs(route.query)
                    record_id = params.get("id", [""])[0]
                    result = read_keyword_record(db, record_id)
                    if result is None:
                        result, status = {"error": "키워드 기록을 찾을 수 없습니다."}, 404
                except ValueError:
                    result, status = {"error": "키워드 기록 ID를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/briefing":
                db = connect(path)
                try:
                    result = read_briefing(db, parse_qs(route.query))
                except ValueError:
                    result, status = {"error": "브리핑 날짜를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/graph":
                db = connect(path)
                try:
                    selected = graph_input(db, parse_qs(route.query))
                    result = {"input": selected, "analysis": graph_service.status(selected)}
                except ValueError:
                    result, status = {"error": "분석 범위와 날짜를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/links" or re.fullmatch(r"/api/links/[0-9a-f]{24}", route.path):
                db = connect(path)
                try:
                    if route.path == "/api/links":
                        result = read_links(db, parse_qs(route.query), analysis_service)
                    else:
                        group = find_link_group(db, route.path.rsplit("/", 1)[1])
                        if group is None:
                            result, status = {"error": "링크 그룹을 찾을 수 없습니다."}, 404
                        else:
                            result = dict(group, comparison=compare_mentions(group), analysis=analysis_service.status(group))
                except ValueError:
                    result, status = {"error": "날짜 또는 페이지 번호를 확인해 주세요."}, 400
                finally:
                    db.close()
                self.send_json(result, status)
                return
            if route.path == "/api/news":
                db = connect(path)
                try:
                    result = read_news(db, parse_qs(route.query))
                except ValueError:
                    status, result = 400, {"error": "올바른 날짜를 선택해 주세요."}
                finally:
                    db.close()
                content = json.dumps(result, ensure_ascii=False).encode()
                mime = "application/json; charset=utf-8"
            elif route.path in {'/workspace.js', '/workspace.css', '/public-data.js', '/observatory-search.js', '/observatory.js', '/observatory.css', '/strategy.js', '/strategy.css', '/news-network.js', '/news-network.css', '/papers.js', '/papers.css', '/risks.js', '/risks.css', '/risk-network.js', '/risk-network.css', '/intelligence.js', '/intelligence.css'}:
                content = (ROOT / 'static' / route.path[1:]).read_bytes()
                if route.path == '/risks.js':
                    content += b'\n' + (ROOT / 'static/risk-network-bootstrap.js').read_bytes()
                mime = 'application/javascript; charset=utf-8' if route.path.endswith('.js') else 'text/css; charset=utf-8'
            elif route.path in {"/graph", "/graph.js", "/archive", "/research", "/research.js", "/simulation", "/simulation.js"}:
                name = {"/graph": "graph.html", "/graph.js": "graph.js", "/archive": "archive.html", "/research": "research.html", "/research.js": "research.js", "/simulation": "simulation.html", "/simulation.js": "simulation.js"}[route.path]
                content = (ROOT / "static" / name).read_bytes()
                mime = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/html; charset=utf-8"
            elif route.path in {'/intelligence','/intelligence/events','/intelligence/topics','/intelligence/decisions',
                                '/intelligence/scenarios','/intelligence/research','/intelligence/operations',
                                '/intelligence/concepts','/intelligence/experiments','/intelligence/profiles','/intelligence/risk_history'}:
                content = (ROOT / 'static/intelligence.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == '/observatory':
                content = (ROOT / 'static/observatory.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == '/risks':
                content = (ROOT / 'static/risks.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == '/papers':
                content = (ROOT / 'static/papers.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            elif route.path == "/mirofish-license":
                content = (ROOT / "vendor/mirofish/LICENSE").read_bytes()
                mime = "text/plain; charset=utf-8"
            elif route.path == "/mirofish-source.zip":
                content, mime = source_bundle(), "application/zip"
            elif route.path == '/news' or (route.path == '/' and route.query):
                content = (ROOT / "static/index.html").read_bytes()
                mime = "text/html; charset=utf-8"
            elif route.path in {'/', '/strategy', '/operations'}:
                content = (ROOT / 'static/strategy.html').read_bytes()
                mime = 'text/html; charset=utf-8'
            else:
                status, content, mime = 404, b"Not found", "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            route = urlparse(self.path)
            intelligence_action = re.fullmatch(r'/api/intelligence/(events|concepts|decisions|scenarios|profiles|experiments|research|query)',route.path)
            match = re.fullmatch(r"/api/links/([0-9a-f]{24})/analyze", route.path)
            is_graph = route.path == "/api/graph/analyze"
            is_question = route.path == "/api/graph/ask"
            is_research = route.path == "/api/research"
            is_simulation = route.path == "/api/simulation"
            is_reach = route.path in ('/api/wiki','/api/article-explanations','/api/agent-reach/read','/api/agent-reach/repair','/api/agent-reach/subscriptions','/api/agent-reach/subscription-toggle')
            is_source = route.path == '/api/sources'
            is_workflow = route.path == '/api/workflows'
            is_improvement = route.path == '/api/improvement'
            is_topic = route.path == '/api/strategy/topics'
            topic_action = re.fullmatch(r'/api/strategy/topics/([^/]+)/(exclude|restore)', route.path)
            is_baseline = route.path == '/api/baseline'
            baseline_action = re.fullmatch(r'/api/baseline/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
            is_paper_refresh = route.path in ('/api/papers/refresh','/api/papers/pipeline')
            is_paper_analysis = route.path == '/api/papers/analyze'
            paper_action = re.fullmatch(r'/api/papers/(.+)/analyze', route.path)
            improvement_action = re.fullmatch(r'/api/improvement/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
            workflow_resume = re.fullmatch(r'/api/workflows/([a-zA-Z0-9_-]+)/resume', route.path)
            is_runtime_start = route.path == "/api/simulation/runtime/start"
            simulation_action = re.fullmatch(r"/api/simulation/([a-zA-Z0-9_-]+)/(start|stop|resume|interview|chat)", route.path)
            resume_match = re.fullmatch(r"/api/research/([a-zA-Z0-9_-]+)/resume", route.path)
            if not is_reach and not intelligence_action and not match and not is_graph and not is_question and not is_research and not resume_match and not is_simulation and not simulation_action and not is_runtime_start and not is_source and not is_workflow and not workflow_resume and not is_improvement and not improvement_action and not is_paper_refresh and not is_paper_analysis and not paper_action and not is_baseline and not baseline_action and not is_topic and not topic_action:
                self.send_json({"error": "Not found"}, 404)
                return
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if (host not in {f"127.0.0.1:{port}", f"localhost:{port}"}
                    or (origin and origin != f"http://{host}")
                    or self.headers.get_content_type() != "application/json"):
                self.send_json({"error": "뉴스 페이지에서 분석을 요청해 주세요."}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > (32768 if is_reach or intelligence_action or is_topic or topic_action or is_question or is_simulation or simulation_action or is_workflow or is_improvement or is_paper_refresh or is_paper_analysis or paper_action else 1024):
                    raise ValueError
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "분석 요청 형식이 올바르지 않습니다."}, 400)
                return
            if route.path == '/api/wiki':
                try:
                    action = payload.get('action', 'refresh')
                    if action == 'configure':result = wiki_service.manage(payload)
                    elif action == 'alias':result = wiki_service.alias(payload)
                    elif action == 'refresh':result = wiki_service.request(payload.get('topic', ''))
                    elif action == 'archive_question':
                        job_id=payload.get('job_id')
                        if not isinstance(job_id,str):raise ValueError('질문 작업 ID가 필요합니다.')
                        job=question_service.get(job_id)
                        if not job or job['status']!='complete':raise ValueError('현재 검토 완료된 질문만 저장할 수 있습니다.')
                        with question_service.db() as db:
                            row=db.execute('SELECT request_json FROM graph_question_jobs WHERE id=?',(job_id,)).fetchone()
                        result=wiki_service.archive_question(job_id,json.loads(row[0])['question'],job['result'])
                    else:raise ValueError('지원하지 않는 위키 작업입니다.')
                    self.send_json(result, 202)
                except ValueError as error:self.send_json({'error': str(error)}, 400)
                except sqlite3.OperationalError:self.send_json({'error': '저장소가 갱신 중입니다. 다시 요청해 주세요.'}, 503)
                return
            if route.path == '/api/article-explanations':
                try:self.send_json(article_service.submit(payload.get('url','')),202)
                except ValueError as error:self.send_json({'error':str(error)},400)
                except RuntimeError as error:self.send_json({'error':str(error)},429)
                return
            if intelligence_action:
                try:
                    result=intelligence_service.mutate(intelligence_action.group(1),payload)
                    self.send_json(result,202 if result.get('run') else 200)
                except (ValueError,TypeError) as error:
                    self.send_json({'error':str(error)},400)
                except RuntimeError as error:
                    self.send_json({'error':str(error)},409)
                return
            if is_topic or topic_action:
                from dynamic_registry import save_manual, set_excluded
                db = sqlite3.connect(path, timeout=15)
                db.row_factory = sqlite3.Row
                try:
                    if topic_action:
                        topic_id, action = topic_action.groups()
                        result = set_excluded(db, unquote(topic_id), action == 'exclude')
                    else:
                        result = save_manual(db, payload)
                    self.send_json({'item': result} if result else {'error': '주제를 찾을 수 없습니다.'},
                                   200 if result else 404)
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                finally:
                    db.close()
                return
            if is_baseline or baseline_action:
                try:
                    if baseline_action:
                        run_id, action = baseline_action.groups()
                        result = getattr(baseline_service, action)(run_id)
                    else:
                        result = baseline_service.start(payload)
                    self.send_json({'run': result}, 202)
                except ValueError as error:
                    self.send_json({'error': str(error)}, 400)
                except RuntimeError as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_paper_refresh or is_paper_analysis or paper_action:
                try:
                    if route.path == '/api/papers/pipeline':
                        self.send_json(paper_pipeline.configure(payload.get('enabled')),200);return
                    ids = [unquote(paper_action.group(1))] if paper_action else payload.get('ids')
                    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
                        raise ValueError('논문 ID 목록 형식이 올바르지 않습니다.')
                    params = parse_qs(route.query)
                    if ids is None and any(params.get(k) for k in ('q','category','sector','analysis_status')):
                        db = connect(path)
                        try:
                            ids = [item['paper_id'] for item in read_papers(db, dict(params, page=['1'],page_size=['50']))['items']]
                        finally:
                            db.close()
                    if is_paper_refresh:
                        result = paper_service.refresh(ids, limit=int(payload.get('limit',20)))
                    else:
                        result = paper_analysis_service.submit(ids[:10] if ids is not None else None, limit=1 if paper_action else int(payload.get('limit',3)))
                    self.send_json(result, 202)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_improvement or improvement_action:
                try:
                    if improvement_action:
                        cycle_id, action = improvement_action.groups()
                        result = getattr(improvement_service, action)(cycle_id)
                    else:
                        settings = dict(payload)
                        scope = {key: values[0] for key, values in parse_qs(route.query).items()}
                        allowed_scope = {'date', 'topic', 'q', 'channel', 'keyword', 'content_type', 'lens', 'terms', 'strategic_keyword', 'impact', 'sort', 'sector'}
                        if set(scope)-allowed_scope:
                            raise ValueError('자기개선 뉴스 범위를 확인해 주세요.')
                        settings['scope'] = scope or settings.get('scope', {})
                        if not isinstance(settings['scope'], dict) or set(settings['scope'])-allowed_scope:
                            raise ValueError('자기개선 뉴스 범위 형식이 올바르지 않습니다.')
                        if any(not isinstance(v, str) or len(v)>2000 for v in settings['scope'].values()):
                            raise ValueError('자기개선 필터 값이 올바르지 않습니다.')
                        if 'full_corpus' in settings and not isinstance(settings['full_corpus'], bool):
                            raise ValueError('전체 뉴스 선택 값이 올바르지 않습니다.')
                        settings.setdefault('full_corpus', True)
                        settings.setdefault('max_rounds', 0)
                        settings.setdefault('interval_seconds', 5)
                        result = improvement_service.start(settings)
                    self.send_json({'run': public_improvement(result)}, 202)
                except (ValueError, RuntimeError, TypeError) as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_workflow or workflow_resume:
                try:
                    if workflow_resume:
                        result = workflow_service.resume(workflow_resume.group(1))
                    else:
                        from strategy import select_strategy_items
                        params = parse_qs(route.query)
                        limit = int(payload.get('limit', 8))
                        if not 1 <= limit <= 24:
                            raise ValueError('분석 사이클은 1~24개 뉴스를 선택해 주세요.')
                        db = connect(path)
                        try:
                            selected = select_strategy_items(db, params)[:limit]
                        finally:
                            db.close()
                        result = workflow_service.create_run(selected, dict(payload, scope={k:v[0] for k,v in params.items()}))
                    self.send_json({'run': result}, 202)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 409)
                return
            if is_reach:
                try:
                    if route.path.endswith('/repair'):result=reach_pipeline.repair(payload.get('limit',50))
                    elif route.path.endswith('/subscriptions'):result=reach_pipeline.add_subscription(payload)
                    elif route.path.endswith('/subscription-toggle'):result=reach_pipeline.toggle(payload.get('id'),payload.get('enabled'))
                    else:result=reach_service.submit(payload.get('url'),payload.get('mode','auto'))
                    self.send_json(result,202)
                except ValueError as error:self.send_json({'error':str(error)},400)
                return
            if is_source:
                try:
                    db = connect(path)
                    try:
                        from strategy import select_strategy_items
                        params = parse_qs(route.query)
                        selected = select_strategy_items(db, params)
                        requested = payload.get('url')
                        urls = list(dict.fromkeys(item['source_url'] for item in selected if item.get('source_url')))
                        if requested:
                            if requested not in urls:
                                raise ValueError('현재 뉴스 범위에 포함된 URL을 선택해 주세요.')
                            urls = [requested]
                        limit = max(1, min(100, int(payload.get('limit', 40))))
                    finally:
                        db.close()
                    self.send_json(dict(source_service.submit(urls[:limit]), available=len(urls)), 202)
                except (ValueError, RuntimeError) as error:
                    self.send_json({'error': str(error)}, 400)
                return
            if is_runtime_start:
                try:
                    from mirofish_runtime import start_runtime
                    start_runtime()
                    self.send_json({"runtime": runtime_status()})
                except (RuntimeError, ValueError, OSError):
                    self.send_json({"error": "엔진을 시작하지 못했습니다. 설치 상태와 .env.mirofish 설정을 확인해 주세요.", "runtime": runtime_status()}, 409)
                return
            if is_simulation or simulation_action:
                try:
                    if is_simulation:
                        db = connect(path)
                        try:
                            items = simulation_news(db, payload, prepared=True)
                        finally:
                            db.close()
                        result = simulation_service.create_run(items, payload)
                    else:
                        run_id, action = simulation_action.groups()
                        if simulation_service.get_run(run_id) is None:
                            self.send_json({"error": "시뮬레이션을 찾을 수 없습니다."}, 404)
                            return
                        if action == "interview":
                            result = simulation_service.interview(run_id, payload.get("agent_id"), payload.get("prompt", ""), payload.get("platform"))
                            self.send_json({"result": result}); return
                        if action == "chat":
                            result = simulation_service.report_chat(run_id, payload.get("message", ""), payload.get("chat_history", []))
                            self.send_json({"result": result}); return
                        result = getattr(simulation_service, action)(run_id)
                    self.send_json({"run": result}, 202)
                except ValueError as error:
                    self.send_json({"error": str(error)}, 400)
                except RuntimeError as error:
                    self.send_json({"error": str(error)}, 409)
                return
            if is_research or resume_match:
                if not research_service.enabled:
                    self.send_json({"error": "외부 분석이 비활성화되어 있습니다."}, 403)
                    return
                try:
                    if resume_match:
                        result = research_service.resume(resume_match.group(1))
                    else:
                        db = connect(path)
                        try:
                            db.execute("BEGIN")
                            rows = [dict(row) for row in joined_articles(db)]
                            rows.extend(unindexed_link_rows(db, rows))
                            rows.extend(hidden_link_rows(db, rows))
                            records = archived_rows(db, {"active": ["1"], "page_size": ["100"]})
                            archive = list(records["items"])
                            for page in range(2, records["total_pages"] + 1):
                                archive.extend(archived_rows(db, {"active": ["1"], "page_size": ["100"], "page": [str(page)]})["items"])
                        finally:
                            db.close()
                        if payload.get('sample_limit') is not None:
                            limit = int(payload['sample_limit'])
                            if not 1 <= limit <= 100:
                                raise ValueError('선택 분석은 1~100개 뉴스로 제한됩니다.')
                            db = connect(path)
                            try:
                                from strategy import select_strategy_items
                                params = parse_qs(route.query)
                                rows = select_strategy_items(db, params)[:limit]
                            finally:
                                db.close()
                            archive = []
                        result = research_service.create_run(rows, archive)
                    self.send_json({"run": result} if result else {"error": "분석 스냅샷을 찾을 수 없습니다."}, 202 if result else 404)
                except (RuntimeError, ValueError) as error:
                    self.send_json({"error": str(error)}, 409)
                return
            if is_question:
                question = payload.get("question", "")
                ids = payload.get("node_ids", [])
                if not isinstance(question, str) or len(question.strip()) < 3 or len(question) > 2000 or not isinstance(ids, list) or len(ids) > 20 or any(not isinstance(i, str) for i in ids):
                    self.send_json({"error": "질문과 선택 키워드를 확인해 주세요."}, 400)
                    return
                try:
                    answer=question_service.ask(question,ids,parse_qs(route.query))
                    self.send_json(answer)
                except (RuntimeError, ValueError) as error:
                    self.send_json({"error": str(error)}, 422)
                except sqlite3.OperationalError:
                    self.send_json({'error':'근거 저장소가 갱신 중입니다. 잠시 후 다시 질문해 주세요.'},503)
                return
            db = connect(path)
            try:
                group = graph_input(db, parse_qs(route.query)) if is_graph else find_link_group(db, match.group(1))
            except ValueError:
                self.send_json({"error": "분석 범위와 날짜를 확인해 주세요."}, 400)
                return
            finally:
                db.close()
            if group is None:
                self.send_json({"error": "링크 그룹을 찾을 수 없습니다."}, 404)
                return
            service = graph_service if is_graph else analysis_service
            if not group.get("mentions"):
                self.send_json({"error": "분석할 메시지가 없습니다."}, 400)
                return
            if not service.enabled:
                self.send_json({"error": "외부 의미 분석이 아직 활성화되지 않았습니다."}, 403)
                return
            try:
                status = service.submit(group)
                self.send_json({"status": status}, 200 if status == "complete" else 202)
            except RuntimeError as error:
                self.send_json({"error": str(error)}, 429)

    return Handler
