"""Operations routes; shared transport checks stay in server_http."""
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs

def get(self, route, params):
    if route.path == '/api/runtime/workers':
        from background_jobs import worker_status
        self.send_json(worker_status(self.services.path))
        return True
    compact_match = re.fullmatch('/api/(baseline|improvement|workflows)(?:/([a-zA-Z0-9_-]+))?', route.path)
    if compact_match and params.get('view', [''])[0] == 'status':
        from status_views import status_response
        kind, run_id = compact_match.groups()
        db = sqlite3.connect(f'file:{Path(self.services.path).resolve()}?mode=ro', uri=True, timeout=15)
        try:
            active_workers = self.services.baseline_service.worker_counts()
            result = status_response(db, kind, run_id, enabled=self.services.baseline_service.enabled if kind == 'baseline' else self.services.workflow_service.enabled, active_workers=active_workers)
            self.send_json(result if result is not None else {'error': '실행을 찾을 수 없습니다.'}, 200 if result is not None else 404, etag=result.get('version') if result else None)
        finally:
            db.close()
        return True
    baseline_match = re.fullmatch('/api/baseline/([a-zA-Z0-9_-]+)', route.path)
    improvement_match = re.fullmatch('/api/improvement/([a-zA-Z0-9_-]+)', route.path)
    workflow_match = re.fullmatch('/api/workflows/([a-zA-Z0-9_-]+)', route.path)
    if route.path == '/api/corpus/status':
        try:
            self.send_json(self.services.corpus_status.get())
        except sqlite3.OperationalError:
            self.send_json({'error': '수집량 집계 중입니다.'}, 503)
        return True
    if route.path == '/api/collector/channels':
        from collector_status import channel_status
        with sqlite3.connect(self.services.path, timeout=0.25) as db:
            db.row_factory = sqlite3.Row
            self.send_json(channel_status(db, self.services.configured_channels(), self.services.ROOT / 'config.json'))
        return True
    if route.path == '/api/runtime/automation':
        from automation_runtime import runtime_status as automation_status
        self.send_json(automation_status())
        return True
    if route.path == '/api/runtime/views':
        from projection_cache import cache_info
        self.send_json(cache_info())
        return True
    if route.path == '/api/runtime/analysis':
        from llm_runtime import runtime_status as analysis_runtime_status
        self.send_json(analysis_runtime_status())
        return True
    if route.path == '/api/baseline' or baseline_match:
        if baseline_match:
            result = self.services.baseline_service.get(baseline_match.group(1))
            self.send_json(result or {'error': '기본 분석 실행을 찾을 수 없습니다.'}, 200 if result else 404)
        else:
            self.send_json({'runs': self.services.baseline_service.list(), 'enabled': self.services.baseline_service.enabled})
        return True
    if route.path == '/api/improvement' or improvement_match:
        if improvement_match:
            result = self.services.public_improvement(self.services.improvement_service.get(improvement_match.group(1)))
            if result:
                result['catalog'] = self.services.improvement_catalog()
            self.send_json(result or {'error': '자기개선 순환을 찾을 수 없습니다.'}, 200 if result else 404)
        else:
            self.send_json({'runs': [self.services.public_improvement(run) for run in self.services.improvement_service.list()], 'enabled': self.services.workflow_service.enabled, 'catalog': self.services.improvement_catalog()})
        return True
    if route.path == '/api/workflows' or workflow_match:
        if workflow_match:
            result = self.services.workflow_service.get_run(workflow_match.group(1))
            self.send_json(result or {'error': '분석 사이클을 찾을 수 없습니다.'}, 200 if result else 404)
        else:
            self.send_json({'enabled': self.services.workflow_service.enabled, 'runs': self.services.workflow_service.list_runs()})
        return True
    return False

def post(self, route, payload):
    is_workflow = route.path == '/api/workflows'
    is_improvement = route.path == '/api/improvement'
    is_baseline = route.path == '/api/baseline'
    baseline_action = re.fullmatch('/api/baseline/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
    improvement_action = re.fullmatch('/api/improvement/([a-zA-Z0-9_-]+)/(pause|resume)', route.path)
    workflow_resume = re.fullmatch('/api/workflows/([a-zA-Z0-9_-]+)/resume', route.path)
    if is_baseline or baseline_action:
        try:
            if baseline_action:
                run_id, action = baseline_action.groups()
                result = getattr(self.services.baseline_service, action)(run_id)
            else:
                result = self.services.baseline_service.start(payload)
            self.send_json({'run': result}, 202)
        except ValueError as error:
            self.send_json({'error': str(error)}, 400)
        except RuntimeError as error:
            self.send_json({'error': str(error)}, 409)
        return True
    if is_improvement or improvement_action:
        try:
            if improvement_action:
                cycle_id, action = improvement_action.groups()
                result = getattr(self.services.improvement_service, action)(cycle_id)
            else:
                settings = dict(payload)
                scope = {key: values[0] for key, values in parse_qs(route.query).items()}
                allowed_scope = {'date', 'topic', 'q', 'channel', 'keyword', 'content_type', 'lens', 'terms', 'strategic_keyword', 'impact', 'sort', 'sector'}
                if set(scope) - allowed_scope:
                    raise ValueError('자기개선 뉴스 범위를 확인해 주세요.')
                settings['scope'] = scope or settings.get('scope', {})
                if not isinstance(settings['scope'], dict) or set(settings['scope']) - allowed_scope:
                    raise ValueError('자기개선 뉴스 범위 형식이 올바르지 않습니다.')
                if any((not isinstance(v, str) or len(v) > 2000 for v in settings['scope'].values())):
                    raise ValueError('자기개선 필터 값이 올바르지 않습니다.')
                if 'full_corpus' in settings and (not isinstance(settings['full_corpus'], bool)):
                    raise ValueError('전체 뉴스 선택 값이 올바르지 않습니다.')
                settings.setdefault('full_corpus', True)
                settings.setdefault('max_rounds', 0)
                settings.setdefault('interval_seconds', 5)
                result = self.services.improvement_service.start(settings)
            self.send_json({'run': self.services.public_improvement(result)}, 202)
        except (ValueError, RuntimeError, TypeError) as error:
            self.send_json({'error': str(error)}, 409)
        return True
    if is_workflow or workflow_resume:
        try:
            if workflow_resume:
                result = self.services.workflow_service.resume(workflow_resume.group(1))
            else:
                from strategy import select_strategy_items
                params = parse_qs(route.query)
                limit = int(payload.get('limit', 8))
                if not 1 <= limit <= 24:
                    raise ValueError('분석 사이클은 1~24개 뉴스를 선택해 주세요.')
                db = self.services.connect(self.services.path)
                try:
                    selected = select_strategy_items(db, params)[:limit]
                finally:
                    db.close()
                result = self.services.workflow_service.create_run(selected, dict(payload, scope={k: v[0] for k, v in params.items()}))
            self.send_json({'run': result}, 202)
        except (ValueError, RuntimeError) as error:
            self.send_json({'error': str(error)}, 409)
        return True
    return False
