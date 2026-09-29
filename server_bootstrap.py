"""Server service assembly, recovery and shutdown, independent of the CLI."""
from server_context import ServerContext
import json
import sqlite3
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from semantic import AnalysisService
from knowledge_graph import analyze_graph
from research import ResearchService
from source_enrichment import SourceService
from mirofish_service import MiroFishService
from mirofish_runtime import runtime_status
from news_repository import joined_articles, hidden_link_rows, unindexed_link_rows

class NewsHTTPServer(ThreadingHTTPServer):
    # Several deferred panel assets arrive together while projections warm.
    request_queue_size = 64


def bootstrap(path, port, queries):
    from task_lifecycle import ServiceScope
    with ServiceScope() as scope:
        return _bootstrap(path, port, queries, scope)


def _bootstrap(path, port, queries, scope):
    rebuild_articles = queries['rebuild_articles']
    CLASSIFICATION_VERSION = queries['CLASSIFICATION_VERSION']
    ROOT = queries['ROOT']
    configured_channels = queries['configured_channels']
    find_link_group = queries['find_link_group']
    graph_input = queries['graph_input']
    read_briefing = queries['read_briefing']
    read_links = queries['read_links']
    simulation_news = queries['simulation_news']
    source_bundle = queries['source_bundle']
    warm_strategy_views = queries['warm_strategy_views']
    path = str(Path(path).resolve())
    from database import prepare_database, open_db
    prepare_database(path, rebuild_articles, joined_articles, CLASSIFICATION_VERSION).close()
    connect = open_db
    from automation_runtime import interrupted_runs
    recovery_candidates = interrupted_runs(path)
    analysis_service = scope.register(AnalysisService(path))
    graph_service = scope.register(AnalysisService(path, analyzer=analyze_graph, table='graph_analysis'))
    research_service = scope.register(ResearchService(path))
    source_service = scope.register(SourceService(path))
    from strategic_workflow import WorkflowService
    workflow_service = scope.register(WorkflowService(path, sources=source_service))
    from recursive_improvement import RecursiveImprovementService
    from improvement_selection import select_improvement_news

    def improvement_selector(settings, tasks, seen_ids):
        db = connect(path)
        try:
            from dynamic_strategy import discovery_followups
            return select_improvement_news(db, settings, list(tasks) + discovery_followups(db), seen_ids)
        finally:
            db.close()
    improvement_service = scope.register(RecursiveImprovementService(path, workflow_service, improvement_selector))

    def public_improvement(run):
        if not run:
            return run
        result = dict(run)
        result['rounds'] = [dict(round_data, snapshot={key: value for key, value in (round_data.get('snapshot') or {}).items() if key in ('identities', 'new_document_count', 'same_snapshot_retry')}) for round_data in run.get('rounds', [])[-24:]]
        result['tasks'] = run.get('tasks', [])[-100:]
        result['rules'] = run.get('rules', [])[-32:]
        result['history_scope'] = '최근 24회차·후속 과제 100개·개선 규칙 32개 표시; 전체 이력은 DB에 보존'
        result['coverage'] = {k: v for k, v in (run.get('coverage') or {}).items() if k != 'corpus_ids'}
        return result

    def improvement_catalog():
        from improvement_memory import list_catalog
        db = connect(path)
        try:
            return list_catalog(db, limit=100)
        finally:
            db.close()
    from arxiv_papers import PaperService, read_papers, paper
    from paper_analysis import PaperAnalysisService
    paper_service = scope.register(PaperService(path, start_worker=False))
    paper_analysis_service = scope.register(PaperAnalysisService(path, start_worker=False))
    from paper_pipeline import PaperPipeline
    paper_pipeline = scope.register(PaperPipeline(path, paper_service, paper_analysis_service, autostart=False))
    from baseline_jobs import BaselineJobs

    def baseline_selector():
        from improvement_selection import all_corpus_items
        db = connect(path)
        try:
            return all_corpus_items(db)
        finally:
            db.close()
    baseline_service = scope.register(BaselineJobs(path, baseline_selector))
    simulation_service = scope.register(MiroFishService(path, readiness=runtime_status))
    from strategic_hub import StrategicHub
    intelligence_service = scope.register(StrategicHub(path, simulation=simulation_service))
    from reach_pipeline import ReachPipeline
    reach_pipeline = scope.register(ReachPipeline(path))
    from graph_questions import GraphQuestions
    question_service = scope.register(GraphQuestions(path, enabled=graph_service.enabled, enricher=reach_pipeline))
    question_service.warm()
    from observatory_runtime import ObservatoryRuntime
    from agent_reach_service import AgentReachService
    reach_service = scope.register(AgentReachService(path))
    from corpus_status import CorpusStatus
    corpus_status = scope.register(CorpusStatus(path))
    corpus_status.get()

    def observatory_status():
        from collector_status import channel_status
        from status_views import status_response
        with sqlite3.connect(path, timeout=2) as db:
            db.row_factory = sqlite3.Row
            collector = channel_status(db, configured_channels(), ROOT / 'config.json')
            base = status_response(db, 'baseline', limit=1)
            deep = status_response(db, 'improvement', limit=1)
        return dict(collector=collector, base=base, deep=deep, corpus=corpus_status.get(), sources=source_service.status())
    observatory_service = scope.register(ObservatoryRuntime(path, status_loader=observatory_status))
    observatory_service.request()
    observatory_service.status()
    from article_explanations import ArticleExplanations
    article_service = scope.register(ArticleExplanations(path, observatory_service, start_worker=False))
    from knowledge_wiki import KnowledgeWiki
    wiki_service = scope.register(KnowledgeWiki(path, start_worker=False))
    from background_jobs import BackgroundJobs
    background_jobs = scope.register(BackgroundJobs(path))
    from server_http import make_handler
    Handler = make_handler(ServerContext(analysis_service=analysis_service, article_service=article_service, baseline_service=baseline_service, connect=connect, corpus_status=corpus_status, graph_service=graph_service, improvement_catalog=improvement_catalog, improvement_service=improvement_service, intelligence_service=intelligence_service, observatory_service=observatory_service, paper=paper, paper_analysis_service=paper_analysis_service, paper_pipeline=paper_pipeline, paper_service=paper_service, path=path, port=port, public_improvement=public_improvement, question_service=question_service, reach_pipeline=reach_pipeline, reach_service=reach_service, read_papers=read_papers, research_service=research_service, simulation_service=simulation_service, source_service=source_service, wiki_service=wiki_service, workflow_service=workflow_service, ROOT=ROOT, configured_channels=configured_channels, find_link_group=find_link_group, graph_input=graph_input, hidden_link_rows=hidden_link_rows, read_briefing=read_briefing, read_links=read_links, simulation_news=simulation_news, source_bundle=source_bundle, unindexed_link_rows=unindexed_link_rows))
    server = NewsHTTPServer(('127.0.0.1', port), Handler)
    warm_stop = threading.Event()
    def warm_views(*args):
        from task_lifecycle import cancellation_scope
        from concurrent.futures import CancelledError
        try:
            with cancellation_scope(warm_stop):
                warm_strategy_views(*args)
        except CancelledError:
            pass
    warm_thread = threading.Thread(target=warm_views, args=(path, warm_stop, source_service.status), daemon=True, name='news-view-warmup')
    from automation_runtime import recover
    try:
        warm_thread.start()
        recover(recovery_candidates, {'baseline': baseline_service, 'improvement': improvement_service, 'workflows': workflow_service})
        print(f'뉴스 페이지: http://127.0.0.1:{port}', flush=True)
        server.serve_forever()
    finally:
        warm_stop.set()
        errors = scope.close()
        server.server_close()
        if warm_thread.ident is not None:
            warm_thread.join(timeout=3)
        if errors:
            print(json.dumps({'shutdown_errors': errors}), flush=True)
