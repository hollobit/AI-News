"""Persistent improvement of evidence selection and review rules, never self-modifying code."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import sqlite3
import threading
import uuid

from link_groups import canonical_url


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def news_identity(item):
    url = canonical_url(item.get('source_url') or '')
    if url:
        return url
    identity = item.get('id') or item.get('message_id')
    return 'news:' + (str(identity) if identity is not None else fingerprint({k: item.get(k) for k in ('title', 'text', 'summary')}))


def news_fingerprint(item):
    source = item.get('source_context') or {}
    return fingerprint({'news': {k: item.get(k) for k in ('title', 'text', 'summary', 'description')},
                        'source_text': str(source.get('text') or '') if source.get('status') == 'fetched' else ''})


def owner_alive(pid):
    try:
        if not pid:
            return False
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def quality_metrics(workflow):
    from graph_rag import validated_workflow_content
    from risk_analysis import validated_risk_content
    result = workflow.get('results') or {}
    report = result.get('report') or {}
    audit = result.get('verification') or {}
    evidence = result.get('evidence') or []
    coverage = result.get('coverage') or workflow.get('coverage') or {}
    claims = report.get('claims') or []
    ids = {e.get('id') for e in evidence}
    refs = {ref for claim in claims for ref in claim.get('evidence_ids', [])}
    grounded = bool(claims) and all(claim.get('evidence_ids') and set(claim['evidence_ids']) <= ids for claim in claims)
    valid = (workflow.get('status') == 'complete' and not workflow.get('error') and result.get('verified') is True and audit.get('accepted') is True
             and not audit.get('issues') and grounded and refs <= set(audit.get('checked_evidence_ids', []))
             and audit.get('report_hash') == fingerprint(report) and audit.get('evidence_hash') == fingerprint(evidence))
    admitted = validated_workflow_content(result, workflow.get('id', '')) if valid else {}
    valid = valid and bool(admitted) and len(admitted.get('claims', [])) == len(claims)
    risk_report = result.get('risk_report') or {}
    risk_audit = result.get('risk_verification') or {}
    risk_verified = bool(validated_risk_content(result, workflow.get('id', '')))
    return {'verified': bool(valid), 'claims': len(claims), 'cited_claims': sum(bool(c.get('evidence_ids')) for c in claims),
            'evidence_count': len(evidence), 'audit_issues': len(audit.get('issues') or []),
            'failed_sources': len(coverage.get('failed_urls') or []),
            'requested_sources': coverage.get('requested_urls', 0), 'fetched_sources': coverage.get('fetched_urls', 0),
            'strategic_concepts': sum(c.get('category') == 'strategic_concept' for c in claims),
            'risk_assessments': len(risk_report.get('risks', [])), 'risk_verified': bool(risk_verified),
            'critical_risks': sum(r.get('current_severity') == 'critical' for r in risk_report.get('risks', [])),
            'risk_issues': len(risk_audit.get('issues') or [])}


class RecursiveImprovementService:
    def __init__(self, path, workflow, selector, *, interval_default=900, catalog_updater=None):
        self.path = str(path)
        self.workflow = workflow
        self.selector = selector
        self.interval_default = interval_default
        self.catalog_updater = catalog_updater
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = False
        self.active = None
        self.thread = None
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS rsi_cycles (
                id TEXT PRIMARY KEY,status TEXT NOT NULL,settings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,next_run_at TEXT,
                pause_requested INTEGER NOT NULL DEFAULT 0,owner_pid INTEGER,
                seen_json TEXT NOT NULL DEFAULT '{}',no_progress INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',coverage_json TEXT NOT NULL DEFAULT '{}',
                state_json TEXT NOT NULL DEFAULT '{}')''')
            db.execute('''CREATE TABLE IF NOT EXISTS rsi_rounds (
                id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,number INTEGER NOT NULL,status TEXT NOT NULL,
                created_at TEXT NOT NULL,completed_at TEXT,workflow_run_id TEXT,
                snapshot_json TEXT NOT NULL,before_json TEXT,after_json TEXT,comparison_json TEXT,
                catalog_json TEXT,error TEXT NOT NULL DEFAULT '',UNIQUE(cycle_id,number))''')
            db.execute('''CREATE TABLE IF NOT EXISTS rsi_tasks (
                id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,source_run_id TEXT NOT NULL,status TEXT NOT NULL,
                created_at TEXT NOT NULL,search_terms_json TEXT NOT NULL DEFAULT '[]')''')
            db.execute('''CREATE TABLE IF NOT EXISTS rsi_rules (
                id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,text TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,source_run_id TEXT NOT NULL,created_at TEXT NOT NULL)''')
            for table, name, default in [('rsi_cycles', 'coverage_json', '{}'), ('rsi_cycles', 'state_json', '{}'), ('rsi_tasks', 'search_terms_json', '[]')]:
                if name not in {r[1] for r in db.execute('PRAGMA table_info('+table+')')}:
                    db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+" TEXT NOT NULL DEFAULT '"+default+"'")
            for row in db.execute("SELECT id,owner_pid FROM rsi_cycles WHERE status IN ('running','waiting','finishing')").fetchall():
                if not owner_alive(row['owner_pid']):
                    db.execute("UPDATE rsi_cycles SET status='paused',pause_requested=1,owner_pid=NULL,updated_at=? WHERE id=?", (now(), row['id']))

    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _settings(self, settings):
        value = dict(settings or {})
        value['interval_seconds'] = max(5, min(int(value.get('interval_seconds', 5 if value.get('full_corpus', True) else self.interval_default)), 86400))
        value['max_rounds'] = max(0, min(int(value.get('max_rounds', 0)), 1000))
        value['max_news'] = max(1, min(int(value.get('max_news', value.get('news_limit', 6))), 24))
        value['max_no_progress'] = max(1, min(int(value.get('max_no_progress', 2)), 10))
        value['poll_seconds'] = max(5, min(int(value.get('poll_seconds', 900)), 86400))
        value.setdefault('full_corpus', True)
        value.setdefault('require_risk', True)
        return value

    def start(self, settings=None):
        with self.lock:
            self._available()
            stamp, cycle_id = now(), uuid.uuid4().hex
            value = self._settings(settings)
            with self.db() as db:
                db.execute('INSERT INTO rsi_cycles(id,status,settings_json,created_at,updated_at,next_run_at,owner_pid) VALUES (?,?,?,?,?,?,?)',
                           (cycle_id, 'running', encoded(value), stamp, stamp, stamp, os.getpid()))
            self._seed_verified(cycle_id)
            self._launch(cycle_id)
        return self.get(cycle_id)

    def _available(self):
        if not getattr(self.workflow, 'enabled', True):
            raise RuntimeError('외부 의미 분석이 아직 활성화되지 않았습니다.')
        if self.closed or self.active:
            raise RuntimeError('개선 사이클이 이미 실행 중이거나 서비스가 종료되었습니다.')
        with self.db() as db:
            live = db.execute("SELECT owner_pid FROM rsi_cycles WHERE status IN ('running','waiting','finishing')").fetchall()
        if any(owner_alive(row[0]) for row in live):
            raise RuntimeError('다른 프로세스에서 개선 사이클이 실행 중입니다.')

    def _launch(self, cycle_id):
        self.active = cycle_id
        self.wake.clear()
        self.thread = threading.Thread(target=self._worker, args=(cycle_id,), daemon=True, name='news-recursive-improvement')
        self.thread.start()

    def pause(self, cycle_id):
        with self.lock:
            cycle = self.get(cycle_id)
            if not cycle:
                raise ValueError('개선 사이클을 찾을 수 없습니다.')
            if cycle['status'] in ('complete', 'paused'):
                return cycle
            pending = any(r['status'] in ('planned', 'running') for r in cycle['rounds'])
            self._update(cycle_id, status='finishing' if pending else 'paused', pause_requested=1)
            self.wake.set()
        return self.get(cycle_id)

    def resume(self, cycle_id):
        with self.lock:
            self._available()
            cycle = self.get(cycle_id)
            if not cycle:
                raise ValueError('개선 사이클을 찾을 수 없습니다.')
            if cycle['status'] == 'complete':
                return cycle
            self._update(cycle_id, status='running', pause_requested=0, next_run_at=now(), owner_pid=os.getpid(), error='')
            self._launch(cycle_id)
        return self.get(cycle_id)

    def _update(self, cycle_id, **fields):
        allowed = {'status', 'pause_requested', 'next_run_at', 'owner_pid', 'error', 'no_progress', 'seen_json', 'coverage_json', 'state_json'}
        if not set(fields) <= allowed:
            raise ValueError('Unsupported cycle update')
        fields['updated_at'] = now()
        with self.db() as db:
            db.execute('UPDATE rsi_cycles SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', (*fields.values(), cycle_id))

    def _seen(self, cycle_id):
        with self.db() as db:
            return json.loads(db.execute('SELECT seen_json FROM rsi_cycles WHERE id=?', (cycle_id,)).fetchone()[0])

    def _state(self, cycle_id):
        with self.db() as db:
            return json.loads(db.execute('SELECT state_json FROM rsi_cycles WHERE id=?', (cycle_id,)).fetchone()[0])

    def _seed_verified(self, cycle_id):
        with self.db() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_runs'").fetchone()
            rows = db.execute("SELECT id FROM strategic_workflow_runs WHERE status='complete'").fetchall() if exists else []
        seen, state = {}, {}
        for row in rows:
            run = self.workflow.get_run(row['id'])
            if not run or not quality_metrics(run)['verified']:
                continue
            refs = {ref for c in run['results']['report']['claims'] for ref in c['evidence_ids']}
            cited_urls = {canonical_url(e.get('url') or '') for e in run['results']['evidence'] if e.get('id') in refs}
            risk_ids = set((run['results'].get('risk_report') or {}).get('assessed_evidence_ids', [])) & set((run['results'].get('risk_verification') or {}).get('checked_evidence_ids', []))
            risk_urls = {canonical_url(e.get('url') or '') for e in run['results']['evidence'] if e.get('id') in risk_ids}
            for evidence in run['results']['evidence']:
                url = canonical_url(evidence.get('url') or '')
                if url and url in cited_urls:
                    seen[url] = 'reused_verified'
                    record = state.setdefault(url, {'status': 'verified', 'reused': True, 'workflow_run_id': run['id'], 'message_texts': [], 'source_texts': [],
                                                    'risk_attempted': 'risk_report' in run['results'], 'risk_assessed': quality_metrics(run)['risk_verified'] and url in risk_urls})
                    field = 'source_texts' if evidence.get('origin') == 'fetched_url_excerpt' else 'message_texts'
                    if field != 'source_texts' or evidence.get('id') in refs:
                        record[field].append(evidence.get('text', ''))
        self._update(cycle_id, seen_json=encoded(seen), state_json=encoded(state))

    def _worker(self, cycle_id):
        try:
            while not self.closed:
                cycle = self.get(cycle_id)
                pending = next((r for r in cycle['rounds'] if r['status'] in ('planned', 'running')), None)
                if pending:
                    if cycle['pause_requested'] and not pending['workflow_run_id']:
                        self._update(cycle_id, status='paused', owner_pid=None)
                        return
                    if self._process_round(cycle, pending) is False:
                        self.wake.wait(1)
                        self.wake.clear()
                        continue
                    continue
                if cycle['pause_requested']:
                    self._update(cycle_id, status='paused', owner_pid=None)
                    return
                settings = cycle['settings']
                if settings['max_rounds'] and len(cycle['rounds']) >= settings['max_rounds']:
                    self._update(cycle_id, status='budget_exhausted', owner_pid=None)
                    return
                due = datetime.fromisoformat(cycle['next_run_at']) if cycle['next_run_at'] else datetime.now(timezone.utc)
                delay = (due - datetime.now(timezone.utc)).total_seconds()
                if delay > 0:
                    self.wake.wait(min(delay, 30))
                    self.wake.clear()
                    continue
                self._plan_round(cycle)
        except Exception as exc:
            self._update(cycle_id, status='error', error=str(exc)[:500], owner_pid=None)
        finally:
            with self.lock:
                if self.active == cycle_id:
                    self.active = None
                if self.closed:
                    self._update(cycle_id, status='paused', pause_requested=1, owner_pid=None)

    def _plan_round(self, cycle):
        cycle_id, settings = cycle['id'], cycle['settings']
        seen = self._seen(cycle_id)
        selection = self.selector(settings, cycle['tasks'], set(seen))
        coverage = dict(getattr(selection, 'coverage', {}))
        items = [dict(item) for item in selection]
        unique = {}
        for item in items:
            unique.setdefault(news_identity(item), item)
        coverage.update(total_unique=coverage.get('total_unique', len(unique)), corpus_ids=list(unique))
        self._update(cycle_id, coverage_json=encoded(coverage))
        state = self._state(cycle_id)
        for key, item in unique.items():
            if seen.get(key) == 'reused_verified':
                old = state.get(key, {})
                text = '\n'.join(str(item.get(k) or '') for k in ('title', 'summary', 'text', 'description')).strip()[:2000]
                source = item.get('source_context') or {}
                current_source = source.get('text') if source.get('status') == 'fetched' else ''
                if text in old.get('message_texts', []) and (not current_source or current_source in old.get('source_texts', [])):
                    seen[key] = news_fingerprint(item)
                else:
                    del seen[key]
                    state.pop(key, None)
        new_items = [item for key, item in unique.items() if seen.get(key) != news_fingerprint(item)]
        for item in new_items:
            state.pop(news_identity(item), None)
        self._update(cycle_id, seen_json=encoded(seen), state_json=encoded(state))
        risk_backfill = [item for key, item in unique.items() if state.get(key, {}).get('status') == 'verified'
                         and not state[key].get('risk_attempted')] if settings.get('require_risk', True) else []
        items = (new_items or list(unique.values()))[:settings['max_news']]
        if risk_backfill:
            items = risk_backfill[:settings['max_news']]
        previous = cycle['rounds'][-1] if cycle['rounds'] else None
        retried = {r['snapshot'].get('retry_of') for r in cycle['rounds']}
        retry_round = None
        if not new_items and not risk_backfill:
            retry_round = next((r for r in cycle['rounds'] if r['status'] in ('needs_review', 'failed')
                                and r['id'] not in retried and not r['snapshot'].get('same_snapshot_retry')
                                and all(key in unique for key in r['snapshot']['identities'])), None)
            if retry_round:
                items = [unique[key] for key in retry_round['snapshot']['identities']][:settings['max_news']]
        signatures = {news_identity(item): news_fingerprint(item) for item in items}
        changed = [key for key, value in signatures.items() if seen.get(key) != value]
        retry = bool(items and retry_round and cycle['tasks'] and not changed)
        if not items or not changed and not retry and not risk_backfill:
            self._update(cycle_id, status='waiting', next_run_at=(datetime.now(timezone.utc) + timedelta(seconds=settings['poll_seconds'])).isoformat(),
                         no_progress=cycle['metrics']['no_progress_rounds']+1,
                         error='새 근거를 기다립니다. 미처리·검토 필요·실패 수는 별도 표시하며 동일 자료의 모델 재요약은 실행하지 않습니다.')
            return
        round_id = uuid.uuid4().hex
        snapshot = {'items': items, 'identities': list(signatures), 'fingerprints': signatures,
                    'new_document_count': len(changed), 'same_snapshot_retry': retry,
                    'risk_backfill': bool(risk_backfill),
                    'retry_of': retry_round['id'] if retry else None,
                    'improvement_context': self._improvement_context(cycle)}
        before = previous['after'] if previous else None
        with self.db() as db:
            db.execute('''INSERT INTO rsi_rounds(id,cycle_id,number,status,created_at,snapshot_json,before_json)
                       VALUES (?,?,?,?,?,?,?)''', (round_id, cycle_id, len(cycle['rounds'])+1, 'planned', now(), encoded(snapshot), encoded(before)))
        self._update(cycle_id, status='running', error='')

    @staticmethod
    def _improvement_context(cycle):
        return {'purpose': '근거 선택·검증 질문·절차 개선; 모델 가중치나 실행 코드를 수정하지 않음',
                'rule_proposals': cycle['rules'][-8:], 'followup_tasks': [t for t in cycle['tasks'] if t['status'] == 'open'][-10:],
                'prior_metrics': cycle['rounds'][-1]['after'] if cycle['rounds'] else None,
                'instruction': 'rule_proposals는 미승격 후보이며 적용 규칙이 아님. 활성 규칙은 별도 request.active_rules만 사용. 이전 해석은 현재 evidence로 재확인.'}

    def _find_workflow(self, round_id):
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='strategic_workflow_runs'").fetchone():
                return None
            rows = db.execute('SELECT id,request_json FROM strategic_workflow_runs ORDER BY created_at DESC').fetchall()
        for row in rows:
            if json.loads(row['request_json']).get('recursive_round_id') == round_id:
                return self.workflow.get_run(row['id'])
        return None

    def _process_round(self, cycle, round_data):
        run = self.workflow.get_run(round_data['workflow_run_id']) if round_data['workflow_run_id'] else self._find_workflow(round_data['id'])
        if not run:
            if cycle['pause_requested'] or self.closed:
                self._update(cycle['id'], status='paused', owner_pid=None)
                return False
            if getattr(self.workflow, 'active', None):
                self._update(cycle['id'], status='waiting', error='다른 분석 사이클 종료를 기다립니다.')
                return False
            request = dict(cycle['settings'])
            request.update(recursive_cycle_id=cycle['id'], recursive_round_id=round_data['id'],
                           improvement_context=round_data['snapshot']['improvement_context'])
            run = self.workflow.create_run(round_data['snapshot']['items'], request)
        if round_data['workflow_run_id'] != run['id']:
            with self.db() as db:
                db.execute("UPDATE rsi_rounds SET workflow_run_id=?,status='running' WHERE id=?", (run['id'], round_data['id']))
        if run['status'] in ('queued', 'running'):
            return False
        if run['status'] == 'paused':
            if not cycle['pause_requested'] and not self.closed and not getattr(self.workflow, 'active', None):
                self.workflow.resume(run['id'])
            return False
        if run['status'] not in ('complete', 'needs_review', 'failed'):
            return False
        self._finish_round(cycle, round_data, run)
        return True

    def _finish_round(self, cycle, round_data, run):
        after = quality_metrics(run)
        before = round_data['before'] or {}
        same = bool(cycle['rounds'][:-1] and set(cycle['rounds'][-2]['snapshot']['identities']) == set(round_data['snapshot']['identities']))
        changes = {key: after[key]-before[key] for key in ('claims', 'evidence_count', 'audit_issues', 'failed_sources', 'strategic_concepts', 'risk_assessments', 'critical_risks', 'risk_issues') if key in before}
        comparison = {'assessment': '관측 지표를 기록했습니다. 일반적인 분석 능력 향상은 입증되지 않았습니다.',
                      'changes': changes, 'comparability': 'same_news_scope' if same else 'different_news_scope' if before else 'baseline',
                      'same_evaluation': '동일한 인용·검토·해시 검증 기준', 'new_document_count': round_data['snapshot']['new_document_count']}
        self._feedback(cycle['id'], run, after)
        updater = self.catalog_updater
        if updater is None:
            from improvement_memory import improve_catalog
            updater = improve_catalog
        with self.db() as db:
            catalog = updater(db, run['id'], run)
            status = 'complete' if after['verified'] else 'failed' if run['status'] == 'failed' else 'needs_review'
            # Catalog suggestions remain open tasks, not executable rules or verified news facts.
            for task in catalog.get('followup_tasks', [])[:12]:
                self._task(db, cycle['id'], run['id'], task.get('kind', 'followup'),
                           task.get('title') or task.get('text') or task.get('reason') or '', task.get('evidence_ids', []), task.get('search_terms', []))
        seen = self._seen(cycle['id'])
        state = self._state(cycle['id'])
        seen.update(round_data['snapshot']['fingerprints'])
        refs = {ref for c in ((run.get('results') or {}).get('report') or {}).get('claims', []) for ref in c.get('evidence_ids', [])}
        cited = [e for e in (run.get('results') or {}).get('evidence', []) if e.get('id') in refs]
        risk_checked = set(((run.get('results') or {}).get('risk_verification') or {}).get('checked_evidence_ids', []))
        risk_checked &= set(((run.get('results') or {}).get('risk_report') or {}).get('assessed_evidence_ids', []))
        risk_evidence = [e for e in (run.get('results') or {}).get('evidence', []) if e.get('id') in risk_checked]
        for key in round_data['snapshot']['identities']:
            item = next(item for item in round_data['snapshot']['items'] if news_identity(item) == key)
            item_text = '\n'.join(str(item.get(k) or '') for k in ('title', 'summary', 'text', 'description')).strip()[:2000]
            cited_item = any(canonical_url(e.get('url') or '') == key for e in cited) if key.startswith('http') else any(e.get('text') == item_text and e.get('origin') == 'telegram_excerpt' for e in cited)
            risk_checked_item = any(canonical_url(e.get('url') or '') == key for e in risk_evidence) if key.startswith('http') else any(e.get('text') == item_text and e.get('origin') == 'telegram_excerpt' for e in risk_evidence)
            state[key] = {'status': 'verified' if after['verified'] and cited_item else 'failed' if run['status'] == 'failed' else 'needs_review', 'reused': False,
                          'risk_attempted': True, 'risk_assessed': after['risk_verified'] and risk_checked_item,
                          'workflow_run_id': run['id']}
        # Incorporate newly fetched source hashes to avoid re-running just because the cache was populated.
        result_evidence = (run.get('results') or {}).get('evidence') or []
        source_text = {e.get('url'): e.get('text') for e in result_evidence if e.get('origin') == 'fetched_url_excerpt'}
        for item in round_data['snapshot']['items']:
            key = news_identity(item)
            if key in source_text:
                context = {'status': 'fetched', 'text': source_text[key], 'content_hash': hashlib.sha256(source_text[key].encode()).hexdigest()}
                seen[key] = news_fingerprint(dict(item, source_context=context))
        fresh_cycle = self.get(cycle['id'])
        # Commit the completed round and its processed-version cursor together.
        # Catalog/task ingestion above is idempotent if shutdown occurs before this checkpoint.
        with self.db() as db:
            db.execute('UPDATE rsi_rounds SET status=?,completed_at=?,after_json=?,comparison_json=?,catalog_json=?,error=? WHERE id=?',
                       (status, now(), encoded(after), encoded(comparison), encoded(catalog), run.get('error') or '', round_data['id']))
            db.execute('''UPDATE rsi_cycles SET seen_json=?,state_json=?,no_progress=?,status=?,next_run_at=?,error='',updated_at=? WHERE id=?''',
                       (encoded(seen), encoded(state), 0 if round_data['snapshot']['new_document_count'] else cycle['metrics']['no_progress_rounds']+1,
                        'paused' if fresh_cycle['pause_requested'] else 'waiting',
                        (datetime.now(timezone.utc) + timedelta(seconds=cycle['settings']['interval_seconds'])).isoformat(), now(), cycle['id']))

    def _task(self, db, cycle_id, run_id, kind, text, evidence_ids, search_terms=None):
        if not text:
            return
        identity = fingerprint([cycle_id, run_id, kind, text])[:24]
        db.execute('INSERT OR IGNORE INTO rsi_tasks VALUES (?,?,?,?,?,?,?,?,?)',
                   (identity, cycle_id, kind, str(text)[:600], encoded(evidence_ids), run_id, 'open', now(), encoded(search_terms or [])))

    def _feedback(self, cycle_id, run, metrics):
        result = run.get('results') or {}
        audit = result.get('verification') or {}
        risk_audit = result.get('risk_verification') or {}
        ids = {e['id'] for e in result.get('evidence', [])}
        checked = [ref for ref in audit.get('checked_evidence_ids', []) if ref in ids]
        coverage = result.get('coverage') or run.get('coverage') or {}
        rules = []
        if coverage.get('failed_urls'):
            rules.append('조회에 실패한 URL 주장은 메시지 발췌 수준으로 한정하고 원문 확인 여부를 별도로 표시한다.')
        if not metrics['verified']:
            rules.append('각 전략 주장에 실제 evidence ID를 연결하고 사실·해석·조건을 구분한 뒤 누락된 인용을 전부 대조한다.')
        if risk_audit.get('issues'):
            rules.append('현재 위협 등급은 관측 지표로, 미래 위험 가능성은 조건부 시나리오로 분리하고 근거 부족은 unknown으로 표시한다.')
        from review_routing import route_review
        with self.db() as db:
            for task in route_review(run):
                self._task(db,cycle_id,run['id'],task['kind'],task['action']+': '+task['reason'],task['evidence_ids'],task.get('search_terms',[]))
            for issue in (risk_audit.get('issues') or [])[:6]:
                self._task(db, cycle_id, run['id'], 'risk_review_gap', str(issue), [ref for ref in risk_audit.get('checked_evidence_ids', []) if ref in ids])
            for issue in (audit.get('issues') or [])[:6]:
                self._task(db, cycle_id, run['id'], 'verification_gap', str(issue), checked)
            for url in coverage.get('failed_urls', [])[:6]:
                self._task(db, cycle_id, run['id'], 'missing_source', '기존 수집 URL 원문 확인: '+url, [])
            if run['status'] == 'failed':
                self._task(db, cycle_id, run['id'], 'execution_failure', '완료하지 못한 분석을 기존 근거 범위에서 한 차례 재검토', [])
            for text in rules:
                identity = fingerprint([cycle_id, text])[:24]
                db.execute('INSERT OR IGNORE INTO rsi_rules VALUES (?,?,?,?,?,?)',
                           (identity, cycle_id, text, encoded(checked), run['id'], now()))

    def get(self, cycle_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM rsi_cycles WHERE id=?', (cycle_id,)).fetchone()
            if not row:
                return None
            rounds = []
            for raw in db.execute('SELECT * FROM rsi_rounds WHERE cycle_id=? ORDER BY number', (cycle_id,)):
                entry = dict(raw)
                for name in ('snapshot', 'before', 'after', 'comparison', 'catalog'):
                    entry[name] = json.loads(entry.pop(name+'_json') or 'null')
                entry['snapshot_ids'] = entry['snapshot']['identities']
                entry['source_count'] = len(entry['snapshot_ids'])
                entry['new_document_count'] = entry['snapshot']['new_document_count']
                rounds.append(entry)
            tasks = [dict(t) for t in db.execute('SELECT * FROM rsi_tasks WHERE cycle_id=? ORDER BY created_at', (cycle_id,))]
            rules = [dict(t) for t in db.execute('SELECT * FROM rsi_rules WHERE cycle_id=? ORDER BY created_at', (cycle_id,))]
        for item in tasks+rules:
            item['evidence_ids'] = json.loads(item.pop('evidence_ids_json'))
            if 'search_terms_json' in item:
                item['search_terms'] = json.loads(item.pop('search_terms_json'))
        coverage = json.loads(row['coverage_json'])
        state = json.loads(row['state_json'])
        scope = set(coverage.get('corpus_ids', state))
        current = {key: value for key, value in state.items() if key in scope}
        processed = len(current)
        total = coverage.get('total_unique', 0)
        return {'id': row['id'], 'status': row['status'], 'settings': json.loads(row['settings_json']),
                'created_at': row['created_at'], 'updated_at': row['updated_at'], 'next_run_at': row['next_run_at'],
                'pause_requested': bool(row['pause_requested']), 'rounds': rounds, 'tasks': tasks, 'rules': rules,
                'metrics': {'round_count': len(rounds), 'completed_rounds': sum(r['status'] == 'complete' for r in rounds),
                            'no_progress_rounds': row['no_progress'], 'seen_documents': len(json.loads(row['seen_json'])),
                            'total_unique': total, 'processed_unique': processed, 'remaining': max(0, total-processed),
                            'verified_unique': sum(v['status'] == 'verified' for v in current.values()),
                            'needs_review_unique': sum(v['status'] == 'needs_review' for v in current.values()),
                            'failed_unique': sum(v['status'] == 'failed' for v in current.values()),
                            'verification_pending': max(0,total-sum(v['status'] == 'verified' for v in current.values())),
                            'risk_assessed_unique': sum(bool(v.get('risk_assessed')) for v in current.values()),
                            'risk_unassessed_unique': max(0, total-sum(bool(v.get('risk_assessed')) for v in current.values())),
                            'reused_verified': sum(bool(v.get('reused')) for v in current.values()),
                            'duplicates_excluded': coverage.get('duplicates_excluded', 0),
                            'all_processed': bool(total) and processed == total,
                            'all_verified': bool(total) and sum(v['status'] == 'verified' for v in current.values()) == total},
                'limits': {'same_snapshot_retry': 1, 'self_modifying_code': False, 'model_weight_training': False,
                           'catalog_requires_verified_evidence': True, 'new_evidence_wait': True}, 'error': row['error']}

    def list(self, limit=12):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM rsi_cycles ORDER BY created_at DESC LIMIT ?', (max(1, min(int(limit), 50)),))]
        return [self.get(identity) for identity in ids]

    def close(self):
        with self.lock:
            if self.active:
                self.pause(self.active)
            self.closed = True
            self.wake.set()
