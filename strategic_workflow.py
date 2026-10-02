"""Bounded role-based analysis over immutable news evidence, with one critique cycle."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
import copy
import hashlib
import json
import os
import sqlite3
import threading
import uuid

from link_groups import canonical_url
from morphology import extract_keywords
from semantic import run_structured
from source_enrichment import SourceService

ROLES = [
    {'id': 'collection', 'title': '수집·중복 정리', 'kind': 'deterministic'},
    {'id': 'enrichment', 'title': 'URL 원문 보강', 'kind': 'retrieval'},
    {'id': 'morphology', 'title': '형태소·키워드 분석', 'kind': 'kiwi'},
    {'id': 'graph_retrieval', 'title': 'GraphRAG 연관 근거 검색', 'kind': 'retrieval'},
    {'id': 'strategy_draft', 'title': '국가·기술 통합 초안', 'kind': 'llm'},
    {'id': 'integrated_reverification', 'title': '통합 최종 재검토', 'kind': 'llm'},
    {'id': 'integrated_analysis', 'title': '통합 전략·위험 분석', 'kind': 'llm'},
    {'id': 'integrated_verification', 'title': '통합 독립 검토', 'kind': 'llm'},
    {'id': 'national', 'title': '국가·정부 전략 분석가', 'kind': 'llm'},
    {'id': 'technology', 'title': '기술·사업 전략 분석가', 'kind': 'llm'},
    {'id': 'risk_assessment', 'title': '현재·미래 위험 평가', 'kind': 'llm'},
    {'id': 'risk_verification', 'title': '위험 근거·등급 검증', 'kind': 'llm'},
    {'id': 'deliberation', 'title': '역할별 관점·이견 정리', 'kind': 'deterministic'},
    {'id': 'synthesis', 'title': '전략 종합', 'kind': 'llm'},
    {'id': 'verification', 'title': '근거 검증', 'kind': 'llm'},
    {'id': 'source_repair', 'title': '실패 원문 재수집', 'kind': 'retrieval'},
    {'id': 'repair_morphology', 'title': '보강 원문 키워드 재분석', 'kind': 'kiwi'},
    {'id': 'strategy_audit_reuse', 'title': '동일 전략 검증 재사용', 'kind': 'deterministic'},
    {'id': 'revision', 'title': '비판 반영·보완', 'kind': 'llm'},
    {'id': 'reverification', 'title': '최종 재검증', 'kind': 'llm'},
    {'id': 'risk_revision', 'title': '위험 평가 보완', 'kind': 'llm'},
    {'id': 'risk_reverification', 'title': '위험 최종 재검증', 'kind': 'llm'},
]
BOUNDS = {'max_news': 24, 'source_workers': 4, 'analyst_workers': 3, 'max_revisions': 1,
          'max_source_repairs': 1, 'max_risks': 6,
          'message_chars': 2000, 'url_chars': 3500, 'scope': 'selected_news_snapshot',
          'external_tools': False, 'continuous_background_collection': False}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


STRING = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STRING}
CLAIM = object_schema({'title': STRING, 'detail': STRING,
                      'category': {'type': 'string', 'enum': ['opportunity', 'risk', 'watch_signal', 'strategic_concept']},
                      'evidence_ids': STRINGS, 'uncertainty': STRING})
REPORT = object_schema({'summary': STRING, 'claims': {'type': 'array', 'items': CLAIM}, 'limitations': STRINGS})
AUDIT = object_schema({'accepted': {'type': 'boolean'}, 'issues': STRINGS,
                       'checked_evidence_ids': STRINGS, 'limitations': STRINGS})


def evidence_schema(schema,evidence):
    """Bind citation syntax to this request without mutating shared role schemas."""
    ids=sorted({entry['id'] for entry in evidence if isinstance(entry.get('id'),str) and entry['id']})
    if not ids:raise ValueError('분석 요청에 유효한 원문 근거 ID가 없습니다.')
    result=copy.deepcopy(schema)
    fields={'evidence_ids','checked_evidence_ids','assessed_evidence_ids','not_assessable_evidence_ids','supporting_evidence_ids','opposing_evidence_ids'}
    def visit(node):
        if isinstance(node,dict):
            for name,value in node.get('properties',{}).items():
                if name in fields and isinstance(value,dict) and value.get('type')=='array':
                    # deepcopy preserves shared object aliases (STRINGS is also used
                    # by issues/limitations). Detach this field before binding IDs.
                    bound=copy.deepcopy(value)
                    bound['items']={'type':'string','enum':list(ids)}
                    node['properties'][name]=bound
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(result)
    return result


def _owner_alive(pid):
    if not isinstance(pid,int) or pid<=0:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def compact_context(context,evidence):
    """Reduce interpretation hints, never the original evidence or audit reports."""
    result=dict(context)
    if result.get('deliberation') and isinstance(result.get('analysts'),dict):
        result['analysts']={role:{key:report[key] for key in ('summary','limitations') if key in report} for role,report in result['analysts'].items()}
    if 'keywords' in result:
        result['keywords']=[{'evidence_id':entry.get('evidence_id'),'keyword_pairs':[
            [term.get('label',''),term.get('surface') or term.get('label','')]
            for term in entry.get('keywords',[])[:8]]} for entry in result['keywords']]
    graph=result.get('graph_context')
    if isinstance(graph,dict):
        found=graph.get('result') or {}
        result['graph_context']={'status':graph.get('status'),'basis':'retrieval_clues_only_not_independent_corroboration',
            'question':str(graph.get('question') or '')[:500],
            'result':{'nodes':[{key:(str(node[key])[:240] if key=='summary' else node[key][:4] if key=='evidence_ids' else node[key])
                for key in ('id','name','type','summary','evidence_ids') if key in node} for node in found.get('nodes',[])[:8]],
                'edges':[{key:edge[key] for key in ('source','target','relation','type') if key in edge} for edge in found.get('edges',[])[:12]],
                'evidence':[{key:(str(entry[key])[:350] if key=='text' else entry[key]) for key in ('id','title','text','source_url','source_kind','evidence_origin') if key in entry}
                    for entry in found.get('evidence',[])[:6]],'limitations':found.get('limitations',[])}}
    request=result.get('request')
    if isinstance(request,dict):
        small={key:request[key] for key in ('question','terms','full_corpus','completion','active_rules') if key in request}
        improvement=request.get('improvement_context') or {}
        ids={entry['id'] for entry in evidence}
        corpus=' '.join(entry.get('text','') for entry in evidence).casefold()
        tasks=[]
        for task in improvement.get('followup_tasks',[]):
            refs=set(task.get('evidence_ids') or [])
            terms=task.get('search_terms') or []
            if refs&ids or any(str(term).casefold() in corpus for term in terms if term):
                tasks.append({key:task[key] for key in ('kind','text','title','reason','evidence_ids','search_terms') if key in task})
        small['improvement_context']={'rule_proposals':improvement.get('rule_proposals',improvement.get('rules',[]))[-4:],
            'followup_tasks':tasks[-4:],'instruction':'과거 해석은 근거가 아니며 현재 원문에서 확인한 관련 지적만 적용한다.'}
        result['request']=small
    return result


class Paused(Exception):
    pass


class WorkflowService:
    """One manual workflow at a time; persisted snapshots and checkpoints support resume."""
    def __init__(self, path, sources=None, analyzer=None, extractor=None, enabled=None, recover_interrupted=True):
        self.path = str(path)
        self.sources = sources or SourceService(path)
        self.owns_sources = sources is None
        self.analyzer = analyzer or run_structured
        self.extractor = extractor or extract_keywords
        self.enabled = os.environ.get('NEWS_EXTERNAL_ANALYSIS_ENABLED') == '1' if enabled is None else enabled
        self.lock = threading.RLock()
        self.closed = False
        self.active = None
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS strategic_workflow_runs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, snapshot_json TEXT NOT NULL, request_json TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE IF NOT EXISTS strategic_workflow_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                stage TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS strategic_workflow_artifacts (
                run_id TEXT NOT NULL, stage TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id,stage))''')
            db.execute('CREATE INDEX IF NOT EXISTS workflow_events_run ON strategic_workflow_events(run_id,seq)')
            db.execute("CREATE INDEX IF NOT EXISTS workflow_round ON strategic_workflow_runs(json_extract(request_json,'$.recursive_round_id'),created_at)")
            # Only this workflow's interrupted rows are touched; other services own their lifecycle.
            if recover_interrupted:
                for row in db.execute("SELECT id,request_json FROM strategic_workflow_runs WHERE status IN ('queued','running')").fetchall():
                    request=json.loads(row['request_json'])
                    # Legacy ownerless runs require explicit operator recovery; an
                    # unknown owner is not proof that a live job may be stopped.
                    owner=request.get('owner_pid')
                    if owner is not None and not _owner_alive(owner):
                        db.execute("UPDATE strategic_workflow_runs SET status='paused',updated_at=? WHERE id=?",(now(),row['id']))

    def db(self):
        from llm_runtime import ClosingConnection
        db = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def _analyze(self, prompt, schema, *, escalation=False):
        result = (self.analyzer(prompt, schema, escalation=escalation)
                  if self.analyzer is run_structured else self.analyzer(prompt, schema))
        provenance = getattr(result, 'provenance', None)
        if provenance and self.active:
            self._save(self.active, 'model_call_' + str(provenance['call_id']), provenance)
        return result

    def _validated_call(self, stage, prompt, schema, validate, escalation=False):
        if not self.active:
            return validate(self._analyze(prompt, schema, escalation=escalation))
        from workflow_efficiency import cached_call
        return cached_call(self, self.active, stage, prompt, schema, validate, escalation=escalation)

    def _revise(self, stage, evidence, report, audit, context, schema, validate, *, risk=False, escalation=False):
        from workflow_patch import revise
        if audit.get('evidence_hash') == digest(evidence):
            patched=revise(self,stage,evidence,report,audit,schema,validate,risk=risk,escalation=escalation)
            if patched is not None:return patched
        return self._validated_call(stage,self._prompt(stage,evidence,context),evidence_schema(schema,evidence),validate,escalation=escalation)

    def _model_provenance(self, run_id):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage LIKE 'model_call_%' ORDER BY stage", (run_id,))]

    def _event(self, run_id, stage, status, detail=''):
        with self.db() as db:
            db.execute('INSERT INTO strategic_workflow_events(run_id,stage,status,created_at,detail) VALUES (?,?,?,?,?)',
                       (run_id, stage, status, now(), detail))

    def _artifact(self, run_id, stage):
        with self.db() as db:
            row = db.execute('SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage=?', (run_id, stage)).fetchone()
        return json.loads(row[0]) if row else None

    def _save(self, run_id, stage, result):
        with self.lock:
            self._check()
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO strategic_workflow_artifacts VALUES (?,?,?)',
                           (run_id, stage, json.dumps(result, ensure_ascii=False)))
            self._event(run_id, stage, 'complete')
        return result

    def _check(self):
        if self.closed:
            raise Paused()

    def _stage(self, run_id, stage, function, dependencies=None):
        self._check()
        saved = self._artifact(run_id, stage)
        expected = digest(dependencies) if dependencies is not None else None
        if saved is not None and (expected is None or self._artifact(run_id, 'input_' + stage) == expected):
            return saved
        self._event(run_id, stage, 'running')
        try:
            result = self._save(run_id, stage, function())
            if expected is not None:self._save(run_id, 'input_' + stage, expected)
            return result
        except Paused:
            raise
        except Exception:
            self._event(run_id, stage, 'failed', '역할 실행 또는 근거 검증 실패')
            raise

    def create_run(self, items, request=None):
        from strategic_value import evaluate_news
        if not self.enabled:
            raise RuntimeError('외부 의미 분석이 아직 활성화되지 않았습니다.')
        from rule_experiments import active_rules
        with self.db() as db:ruleset=active_rules(db)
        request=dict(request or {},active_rules=ruleset)
        snapshot, seen = [], set()
        for raw in items:
            item = dict(raw)
            url = canonical_url(item.get('source_url') or '')
            text = '\n'.join(str(item.get(k) or '') for k in ('title', 'summary', 'text', 'description')).strip()[:2000]
            if not text:
                continue
            signature = url or hashlib.sha256(text.casefold().encode()).hexdigest()
            if signature in seen:
                continue
            seen.add(signature)
            article_date=''
            if item.get('date_basis')=='article':
                try:article_date=date.fromisoformat(str(item.get('day') or '')).isoformat()
                except ValueError:pass
            payload = {'text': text, 'url': url, 'published_at': item.get('published_at') or item.get('day') or '',
                       'title': str(item.get('title') or '')[:300], 'channel': str(item.get('channel') or ''),
                       'message_id': str(item.get('message_id') or item.get('id') or ''), 'origin': item.get('origin') or 'telegram_excerpt',
                       'date_basis':str(item.get('date_basis') or 'unknown'), 'article_date':article_date,
                       'telegram_published_at':str(item.get('published_at') or ''),
                       'strategic_value': evaluate_news(item)}
            payload['id'] = 'news_' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
            snapshot.append(payload)
            if len(snapshot) >= BOUNDS['max_news']:
                break
        if not snapshot:
            raise ValueError('분석할 뉴스가 없습니다.')
        with self.lock:
            if self.closed or self.active:
                raise RuntimeError('전략 워크플로가 실행 중이거나 종료되었습니다.')
            run_id = uuid.uuid4().hex
            stamp = now()
            with self.db() as db:
                db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?,?,?)',
                           (run_id, 'queued', stamp, stamp, json.dumps(snapshot, ensure_ascii=False), json.dumps(dict(request or {},owner_pid=os.getpid()), ensure_ascii=False), ''))
            self._event(run_id, 'collection', 'complete', f'선택 뉴스 {len(snapshot)}개 중복 제거·스냅샷 고정')
            self._start(run_id)
        return self.get_run(run_id)

    def _start(self, run_id):
        self.active = run_id
        threading.Thread(target=self._work, args=(run_id,), daemon=True, name='news-strategic-workflow').start()

    def resume(self, run_id):
        with self.lock:
            if not self.enabled or self.closed or self.active:
                raise RuntimeError('지금 워크플로를 재개할 수 없습니다.')
            run = self.get_run(run_id)
            if not run:
                raise ValueError('워크플로를 찾을 수 없습니다.')
            if run['status'] not in ('paused', 'failed'):
                raise ValueError('일시 정지되거나 실패한 실행만 재개할 수 있습니다.')
            with self.db() as db:
                row=db.execute('SELECT request_json FROM strategic_workflow_runs WHERE id=?',(run_id,)).fetchone()
                request=json.loads(row['request_json']);request['owner_pid']=os.getpid()
                db.execute('UPDATE strategic_workflow_runs SET request_json=? WHERE id=?',(json.dumps(request,ensure_ascii=False),run_id))
            self._status(run_id, 'queued')
            self._start(run_id)
        return self.get_run(run_id)

    def _status(self, run_id, status, error=''):
        with self.lock:
            if self.closed and status != 'paused':
                raise Paused()
            with self.db() as db:
                db.execute('UPDATE strategic_workflow_runs SET status=?,updated_at=?,error=? WHERE id=?', (status, now(), error, run_id))

    def _report(self, value, evidence):
        ids = {e['id'] for e in evidence}
        if not isinstance(value, dict) or not isinstance(value.get('summary'), str) or not isinstance(value.get('claims'), list):
            raise ValueError('전략 결과 형식 오류')
        if not isinstance(value.get('limitations'), list) or not all(isinstance(v, str) for v in value['limitations']):
            raise ValueError('한계 설명 형식 오류')
        if not value['claims'] or len(value['claims']) > 32:
            raise ValueError('전략 결과 개수 오류')
        for claim in value['claims']:
            if not isinstance(claim, dict) or not all(isinstance(claim.get(k), str) and claim[k].strip() for k in ('title', 'detail', 'uncertainty')):
                raise ValueError('전략 결과 필수 항목 오류')
            if claim.get('category') not in CLAIM['properties']['category']['enum']:
                raise ValueError('전략 결과 유형 오류')
            refs = claim.get('evidence_ids')
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in ids for ref in refs):
                raise ValueError('존재하지 않는 근거 인용')
        if 'event_observations' in value:
            from event_observations import validate_events
            validate_events(value['event_observations'],evidence)
        return value

    def _prompt(self, role, evidence, context, **options):
        from workflow_prompts import render
        return render(role,evidence,context,**options)

    def _reusable_strategy_audit(self,audit,evidence,report,require_all_news=False,deliberation=None):
        if deliberation is not None:
            from deliberation import validate_checks
            if audit.get('deliberation_hash')!=digest(deliberation) or validate_checks(audit,deliberation,evidence):return False
        if 'event_observations' in report:
            from event_observations import events_audit_issues
            if audit.get('event_hash')!=digest(report['event_observations']) or events_audit_issues(audit,report['event_observations'],evidence):return False
        try:self._report(report,evidence)
        except (TypeError,ValueError,KeyError):return False
        ids={entry['id'] for entry in evidence}
        refs={ref for claim in report['claims'] for ref in claim['evidence_ids']}
        checked=audit.get('checked_evidence_ids')
        required={entry['id'] for entry in evidence if entry.get('origin')=='telegram_excerpt'} if require_all_news else set()
        return (audit.get('accepted') is True and audit.get('issues')==[]
                and audit.get('verification_version')=='grounded-strategy-v2'
                and audit.get('report_hash')==digest(report) and audit.get('evidence_hash')==digest(evidence)
                and isinstance(checked,list) and all(isinstance(ref,str) and ref in ids for ref in checked)
                and refs<=set(checked) and required<=refs)

    def _reusable_risk_audit(self, audit, evidence, report, require_all_news=False):
        required = set(report.get('assessed_evidence_ids', []))
        if require_all_news:required.update(report.get('not_assessable_evidence_ids', []))
        required.update(ref for risk in report.get('risks', []) for ref in risk.get('evidence_ids', []))
        return (audit.get('accepted') is True and not audit.get('issues')
                and audit.get('verification_version') == 'grounded-risk-v2'
                and audit.get('report_hash') == digest(report)
                and audit.get('evidence_hash') == digest(evidence)
                and required <= set(audit.get('checked_evidence_ids', [])))

    def _audit(self, evidence, report, risk=False, require_all_news=False, deliberation=None):
        audit_context={'risk_report' if risk else 'report':report}
        schema=AUDIT
        if deliberation is not None and not risk:
            from deliberation import audit_schema
            audit_context['deliberation']=deliberation
            schema=audit_schema(AUDIT,deliberation)
        if not risk and 'event_observations' in report:
            from event_observations import audit_schema as event_audit_schema
            schema=event_audit_schema(schema,report['event_observations'])
        from workflow_patch import audit_schema as patch_audit_schema, targets
        schema=patch_audit_schema(schema,report,risk)
        audit_context['revision_target_map']={key:index for key,(index,_) in targets(report,risk).items()}
        if require_all_news:audit_context['request']={'completion':True}
        critical = risk and any(r.get('current_severity') in ('high','critical') for r in report.get('risks',[]))
        return self._validated_call('risk_verification' if risk else 'verification',
            self._prompt('risk_verification' if risk else 'verification', evidence, audit_context, extra_instructions='각 issues의 번호(issue_index, 0부터)에 대해 수정할 target_id와 fields를 revision_targets에 지정한다. 전역 누락·구조 문제는 빈 배열로 남긴다. 통과 시 빈 배열이다.'), evidence_schema(schema,evidence),
            lambda value: self._validate_audit(value, evidence, report, risk, require_all_news, deliberation), escalation=critical)

    def _validate_audit(self, audit, evidence, report, risk=False, require_all_news=False, deliberation=None):
        all_ids = {e['id'] for e in evidence}
        cited = {ref for claim in report['risks' if risk else 'claims'] for ref in claim['evidence_ids']}
        if risk:
            cited.update(report.get('assessed_evidence_ids', []))
            if require_all_news:cited.update(report.get('not_assessable_evidence_ids', []))
        if not isinstance(audit, dict):
            audit = {}
        issues = audit.get('issues')
        checked = audit.get('checked_evidence_ids')
        valid = isinstance(issues, list) and all(isinstance(v, str) for v in issues)
        valid = valid and isinstance(checked, list) and all(isinstance(ref, str) and ref in all_ids for ref in checked)
        valid = valid and isinstance(audit.get('limitations'), list) and all(isinstance(v, str) for v in audit.get('limitations', []))
        if not valid:
            return {'accepted': False, 'issues': ['검증 결과 형식 또는 근거 ID가 유효하지 않습니다.'], 'checked_evidence_ids': [], 'limitations': ['검증 불완전']}
        issues = list(issues)
        if not risk and 'event_observations' in report:
            from event_observations import events_audit_issues
            issues.extend(events_audit_issues(audit,report['event_observations'],evidence))
            audit=dict(audit,event_hash=digest(report['event_observations']))
        if deliberation is not None and not risk:
            from deliberation import validate_checks
            issues.extend(validate_checks(audit,deliberation,evidence))
            audit=dict(audit,deliberation_hash=digest(deliberation))
        if require_all_news and not risk:
            required={entry['id'] for entry in evidence if entry.get('origin')=='telegram_excerpt'}
            if not required<=cited:
                issues.append('전수 범위에서 일부 뉴스의 근거 연결 주장이 누락되었습니다: '+', '.join(sorted(required-cited)))
        if not cited.issubset(set(checked)):
            issues.append('종합 결과의 모든 인용 근거를 대조하지 않았습니다.')
        return dict(audit, accepted=audit.get('accepted') is True and not issues, issues=issues,
                    report_hash=digest(report), evidence_hash=digest(evidence),
                    verification_version="grounded-risk-v2" if risk else "grounded-strategy-v2")

    def _current_audit(self, audit, evidence, report, required_checked=(), deliberation=None):
        if audit.get('report_hash') != digest(report) or audit.get('evidence_hash') != digest(evidence):
            return dict(audit, accepted=False, issues=list(audit.get('issues', [])) + ['현재 근거·종합 결과와 검증 스냅샷이 일치하지 않습니다.'])
        if not set(required_checked)<=set(audit.get('checked_evidence_ids') or []):
            return dict(audit,accepted=False,issues=list(audit.get('issues') or [])+['전수 위험 검토에서 평가 불가로 분류한 근거의 독립 대조가 누락되었습니다.'])
        if 'event_observations' in report:
            from event_observations import events_audit_issues
            issues=events_audit_issues(audit,report['event_observations'],evidence)
            if audit.get('event_hash')!=digest(report['event_observations']):issues.append('사건 검토 해시가 일치하지 않습니다.')
            if issues:return dict(audit,accepted=False,issues=list(audit.get('issues') or [])+issues)
        if deliberation is not None:
            from deliberation import validate_checks
            issues=validate_checks(audit,deliberation,evidence)
            if audit.get('deliberation_hash')!=digest(deliberation):issues.append('현재 역할 입장과 이견 검토 스냅샷이 일치하지 않습니다.')
            if issues:return dict(audit,accepted=False,issues=list(audit.get('issues') or [])+issues)
        return audit

    def _enrich(self, snapshot, retry=None):
        urls = list(dict.fromkeys(e['url'] for e in snapshot if e['url']))
        from link_groups import extract_links
        retrieval_urls = {}
        for item in snapshot:
            target = item.get('url')
            for raw_url in extract_links(item.get('text') or ''):
                if target and canonical_url(raw_url) == target:
                    retrieval_urls.setdefault(target, raw_url)
        retry_urls = set(retry['coverage']['failed_urls']) if retry is not None else None
        def fetch(url):
            try:
                retrieval_url = retrieval_urls.get(url, url)
                result = dict(self.sources.fetch(retrieval_url, refresh=True) if retry is not None else self.sources.fetch(retrieval_url))
                result['text'] = str(result.get('text') or '')[:3500]
                result['url'] = url
                return result
            except Exception:
                return {'url': url, 'status': 'failed', 'error': '원문 조회 실패', 'text': ''}
        with ThreadPoolExecutor(max_workers=4) as pool:
            fetched = list(pool.map(fetch, [url for url in urls if retry_urls is None or url in retry_urls]))
        previous = {s['url']: s for s in retry['sources']} if retry is not None else {}
        previous.update({s['url']: s for s in fetched})
        sources = [previous[url] for url in urls]
        evidence = list(snapshot)
        for source in sources:
            if source.get('status') == 'fetched' and source.get('text'):
                evidence.append({'id': 'url_' + hashlib.sha256(source['url'].encode()).hexdigest()[:24],
                                 'text': source['text'], 'url': source['url'], 'origin': 'fetched_url_excerpt',
                                 'fetched_at': source.get('fetched_at', ''), 'title': source.get('title', '')})
        stale = []
        for source in sources:
            try:
                date = datetime.fromisoformat(source.get('fetched_at', ''))
                if date.tzinfo is None or date < datetime.now(timezone.utc) - timedelta(days=7):
                    stale.append(source['url'])
            except (ValueError, TypeError):
                stale.append(source['url'])
        return {'evidence': evidence, 'sources': sources, 'coverage': {
            'news': len(snapshot), 'requested_urls': len(urls),
            'fetched_urls': sum(s.get('status') == 'fetched' and bool(s.get('text')) for s in sources),
            'failed_urls': [s['url'] for s in sources if s.get('status') != 'fetched' or not s.get('text')],
            'stale_or_undated_urls': stale, 'no_url_news': sum(not e['url'] for e in snapshot),
            'scope': '선택된 뉴스 표본과 URL 부분 발췌; 전체 뉴스·시장 성장률을 대표하지 않습니다.'}}

    def _retrieve_graph(self, snapshot, request):
        from graph_rag import load_integrated_graph, build_retrieval
        question = str(request.get('question') or request.get('terms') or ' '.join(e['title'] for e in snapshot))[:1000]
        try:
            if request.get('completion') and not request.get('question') and not request.get('terms'):
                from workflow_retrieval import retrieve
                result = retrieve(self.path, question)
            else:
                with self.db() as db:
                    graph = load_integrated_graph(db, {'max_nodes': ['24']}, for_retrieval=True)
                    result = build_retrieval(graph, question)
            result['evidence'] = [dict(e, text=str(e.get('text') or '')[:700]) for e in result.get('evidence', [])[:12]]
            return {'status': 'complete', 'question': question, 'result': result,
                    'basis': 'retrieval_clues_only_not_independent_corroboration'}
        except (sqlite3.Error, KeyError):
            return {'status': 'unavailable', 'question': question, 'result': {},
                    'limitation': '저장 그래프 검색에 필요한 자료가 없습니다.'}

    def _work(self, run_id):
        try:
            from risk_analysis import RISK_SCHEMA, validate_risk_report
            self._status(run_id, 'running')
            with self.db() as db:
                row = db.execute('SELECT snapshot_json,request_json FROM strategic_workflow_runs WHERE id=?', (run_id,)).fetchone()
                snapshot, request = json.loads(row[0]), json.loads(row[1])
            enrichment = self._stage(run_id, 'enrichment', lambda: self._enrich(snapshot))
            evidence = enrichment['evidence']
            from workflow_routing import select
            plan=select(snapshot,request,enrichment)
            self._save(run_id,'execution_plan',plan)
            if plan['path']=='compact-v1':
                from workflow_compact import execute
                execute(self,run_id,evidence,request,enrichment)
                return
            if plan['path']=='parallel-drafts-v1':
                from workflow_complex import execute
                execute(self,run_id,evidence,request,enrichment)
                return
            # Risk generation consumes current evidence/coverage, not graph hints.
            risk_context = {'coverage': enrichment['coverage'], 'request': request}
            with ThreadPoolExecutor(max_workers=3) as pool:
                risk_future=pool.submit(self._stage,run_id,'risk_assessment',lambda:self._validated_call('risk_assessment',
                    self._prompt('risk_assessment',evidence,risk_context),evidence_schema(RISK_SCHEMA,evidence),lambda value:validate_risk_report(value,evidence)))
                keywords = self._stage(run_id, 'morphology', lambda: [
                    {'evidence_id': e['id'], 'keywords': self.extractor(e['text'])[:40]} for e in evidence])
                retrieval_dependencies = None
                if request.get('completion') and not request.get('question') and not request.get('terms'):
                    from workflow_retrieval import input_revision
                    with self.db() as db: retrieval_dependencies = [input_revision(db), snapshot, request]
                retrieval = self._stage(run_id, 'graph_retrieval', lambda: self._retrieve_graph(snapshot, request), dependencies=retrieval_dependencies)
                context = {'keywords': keywords, 'coverage': enrichment['coverage'], 'graph_context': retrieval, 'request': request}
                def analyze(role):
                    return self._stage(run_id, role, lambda: self._validated_call(role, self._prompt(role, evidence, context), evidence_schema(REPORT,evidence), lambda value:self._report(value,evidence)))
                futures = {role: pool.submit(analyze, role) for role in ('national', 'technology')}
                reports = {role: future.result() for role, future in futures.items()}
                risk_report=risk_future.result()
            from deliberation import build_deliberation
            deliberation=self._stage(run_id,'deliberation',lambda:build_deliberation(reports,evidence))
            context=dict(context,deliberation=deliberation)
            from event_observations import report_schema as event_report_schema
            synthesis_schema=event_report_schema(REPORT,evidence)
            require_all_news=bool(request.get('full_corpus') or request.get('completion'))
            context = dict(context, risk_report=risk_report, risk_review_status='pending')
            # Independent risk review and strategy synthesis share the same frozen observations.
            with ThreadPoolExecutor(max_workers=2) as pool:
                risk_future = pool.submit(self._stage, run_id, 'risk_verification', lambda: self._audit(evidence, risk_report, risk=True, require_all_news=require_all_news))
                report_future = pool.submit(self._stage, run_id, 'synthesis', lambda: self._validated_call('synthesis',
                    self._prompt('synthesis', evidence, dict(context, analysts=reports)), evidence_schema(synthesis_schema,evidence),lambda value:self._report(value,evidence)))
                risk_audit, report = risk_future.result(), report_future.result()
            risk_audit = self._current_audit(risk_audit, evidence, risk_report, required_checked=risk_report.get('not_assessable_evidence_ids',[]) if require_all_news else ())
            audit = self._stage(run_id, 'verification', lambda: self._audit(evidence, report, require_all_news=require_all_news, deliberation=deliberation))
            audit = self._current_audit(audit, evidence, report, deliberation=deliberation)
            if not audit['accepted'] or not risk_audit['accepted']:
                if enrichment['coverage']['failed_urls']:
                    # A real retrieval action supplies a fresh observation before the only revision.
                    # Checkpointing prevents refresh requests from repeating after a resume.
                    enrichment = self._stage(run_id, 'source_repair', lambda: self._enrich(snapshot, retry=enrichment))
                    evidence = enrichment['evidence']
                    keywords = self._stage(run_id, 'repair_morphology', lambda: [
                        {'evidence_id': e['id'], 'keywords': self.extractor(e['text'])[:40]} for e in evidence])
                    context = dict(context, keywords=keywords, coverage=enrichment['coverage'],
                                   source_repair={'attempts': 1, 'scope': 'failed_snapshot_urls_only',
                                                  'graph_context_reused': '기존 검색은 해석 단서이며 새 원문은 evidence로 직접 제공'})
                if not risk_audit['accepted'] or risk_audit.get('evidence_hash') != digest(evidence):
                    risk_report = self._stage(run_id, 'risk_revision', lambda: self._revise('risk_revision',evidence,risk_report,risk_audit,dict(context,risk_report=risk_report,critique=risk_audit),RISK_SCHEMA,lambda value:validate_risk_report(value,evidence),risk=True))
                # New observations invalidate the old risk audit even when its report did not change.
                risk_audit = self._stage(run_id, 'risk_reverification',
                    lambda: dict(risk_audit, reused=True, reuse_reason='unchanged_evidence_report_and_verification_version')
                    if self._reusable_risk_audit(risk_audit, evidence, risk_report, require_all_news)
                    else self._audit(evidence, risk_report, risk=True, require_all_news=require_all_news))
                risk_audit = self._current_audit(risk_audit, evidence, risk_report, required_checked=risk_report.get('not_assessable_evidence_ids',[]) if require_all_news else ())
                context = dict(context, risk_report=risk_report, risk_review_status='accepted' if risk_audit['accepted'] else 'needs_review', risk_critique=risk_audit)
                # Changing only the risk interpretation does not invalidate an independently
                # accepted strategy report on identical evidence. New source content always does.
                if self._reusable_strategy_audit(audit,evidence,report,require_all_news,deliberation):
                    audit=self._stage(run_id,'strategy_audit_reuse',lambda:dict(audit,reused=True,
                        reuse_reason='unchanged_evidence_report_and_verification_version'))
                else:
                    report = self._stage(run_id, 'revision', lambda: self._revise('revision',evidence,report,audit,dict(context,report=report,critique=audit),event_report_schema(REPORT,evidence),lambda value:self._report(value,evidence),escalation=request.get('completion_attempt',1)>=2))
                    audit = self._stage(run_id, 'reverification', lambda: self._audit(evidence, report, require_all_news=require_all_news, deliberation=deliberation))
                    audit = self._current_audit(audit, evidence, report, deliberation=deliberation)
            self._save(run_id, 'final', {'report': report, 'verification': audit,
                                      **({'event_observations':report['event_observations']} if 'event_observations' in report else {}),
                                      'deliberation':deliberation,
                                      'verified': audit['accepted'] and risk_audit['accepted'], 'evidence': evidence,
                                      'risk_report': risk_report, 'risk_verification': risk_audit, 'risk_verified': risk_audit['accepted'],
                                      'coverage': enrichment['coverage'],
                                      'graph_retrieval': retrieval,
                                      'orchestration': {'pattern': 'observation_retrieval_analysis_review_one_repair',
                                                        'inspired_by': 'MiroFish ReportAgent ReAct',
                                                        'mirofish_simulation_executed': False},
                                      'model_provenance': self._model_provenance(run_id),
                                      'basis': 'snapshot_excerpt_analysis', 'completed_at': now()})
            from review_routing import route_review
            self._save(run_id,'review_plan',route_review({'id':run_id,'results':{'verification':audit,'risk_verification':risk_audit,'evidence':evidence,'coverage':enrichment['coverage']}}))
            self._status(run_id, 'complete' if audit['accepted'] and risk_audit['accepted'] else 'needs_review')
        except Paused:
            self._status(run_id, 'paused')
        except Exception as exc:
            try:
                self._status(run_id, 'failed', str(exc)[:500])
                from review_routing import route_review
                self._save(run_id,'review_plan',route_review(self.get_run(run_id)))
            except Paused:
                pass
        finally:
            with self.lock:
                if self.active == run_id:
                    self.active = None

    def get_run(self, run_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM strategic_workflow_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                return None
            events = [dict(e) for e in db.execute('SELECT * FROM strategic_workflow_events WHERE run_id=? ORDER BY seq', (run_id,))]
            artifacts = {r['stage']: json.loads(r['payload_json']) for r in db.execute('SELECT * FROM strategic_workflow_artifacts WHERE run_id=?', (run_id,))}
        stages = {role['id']: 'pending' for role in ROLES}
        for event in events:
            stages[event['stage']] = event['status']
        compact = (artifacts.get('execution_plan') or {}).get('path') in ('compact-v1','parallel-drafts-v1')
        if compact:
            for stage in ('national', 'technology', 'deliberation', 'graph_retrieval', 'morphology'):
                stages[stage] = 'skipped'
            route=(artifacts.get('execution_plan') or {}).get('path')
            stages['strategy_draft' if route=='compact-v1' else 'integrated_analysis']='skipped'
            if row['status'] in ('complete','needs_review') and 'integrated_reverification' not in artifacts:
                stages['integrated_reverification']='skipped'
        elif row['status'] in ('complete', 'needs_review'):
            for stage in ('integrated_analysis', 'integrated_verification', 'strategy_draft', 'integrated_reverification'):
                stages[stage] = 'skipped'
        if row['status'] in ('complete', 'needs_review') and 'revision' not in artifacts:
            stages['revision'] = stages['reverification'] = 'skipped'
        if row['status'] in ('complete', 'needs_review') and 'source_repair' not in artifacts:
            stages['source_repair'] = stages['repair_morphology'] = 'skipped'
        if row['status'] in ('complete', 'needs_review'):
            for stage in ('risk_revision', 'risk_reverification'):
                if stage not in artifacts:
                    stages[stage] = 'skipped'
            if 'risk_assessment' not in artifacts:
                stages['risk_assessment'] = stages['risk_verification'] = 'not_assessed'
        return {'id': row['id'], 'status': row['status'], 'created_at': row['created_at'], 'updated_at': row['updated_at'],
                'request': json.loads(row['request_json']), 'snapshot_count': len(json.loads(row['snapshot_json'])),
                'error': row['error'], 'roles': ROLES, 'stages': stages, 'events': events,
                'results': artifacts.get('final'), 'coverage': artifacts.get('source_repair', artifacts.get('enrichment', {})).get('coverage', {}),
                'artifacts': {k: v for k, v in artifacts.items() if k not in ('enrichment', 'source_repair', 'final')}, 'bounds': BOUNDS}

    def list_runs(self, limit=12):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM strategic_workflow_runs ORDER BY created_at DESC LIMIT ?', (max(1, min(int(limit), 50)),))]
        return [self.get_run(run_id) for run_id in ids]

    def close(self):
        with self.lock:
            self.closed = True
            if self.active:
                self._status(self.active, 'paused')
        if self.owns_sources:
            self.sources.close()
