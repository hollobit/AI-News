"""Resumable bounded-worker completion of a frozen corpus using the existing role gates.

Run only after the interactive RSI cycle has checkpointed. Results retain the
normal workflow artifacts and independent audits; the corpus ledger never
creates a model verdict. A failed group is retried in smaller groups with its
actual review issues, up to three attempts per document.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
import uuid

from completion_quality import document_admission, observed_item
from recursive_improvement import news_identity, news_fingerprint, now, encoded, quality_metrics, owner_alive


def connect(path):
    from llm_runtime import ClosingConnection
    db = sqlite3.connect(path, timeout=60, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    return db


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS corpus_completion_documents (
        cycle_id TEXT NOT NULL, document_id TEXT NOT NULL, position INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        workflow_run_id TEXT, admission_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL, PRIMARY KEY(cycle_id,document_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS corpus_engine_recoveries (
        cycle_id TEXT NOT NULL, document_id TEXT NOT NULL, prior_json TEXT NOT NULL,
        recovered_at TEXT NOT NULL, PRIMARY KEY(cycle_id,document_id))''')

    db.execute('CREATE INDEX IF NOT EXISTS completion_queue ON corpus_completion_documents(cycle_id,status,position)')
    db.execute('''CREATE TABLE IF NOT EXISTS completion_engine_waits (
        round_id TEXT PRIMARY KEY, failures INTEGER NOT NULL, next_attempt_at REAL NOT NULL,
        error TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS completion_engine_events (
        seq INTEGER PRIMARY KEY, cycle_id TEXT, round_id TEXT, error TEXT, created_at TEXT)''')
    db.execute("CREATE INDEX IF NOT EXISTS completion_source_url ON corpus_completion_documents(json_extract(snapshot_json,'$.source_url'))")


def recover_engine_failures(db, cycle_id):
    """One explicit recovery allowance for engine failures, never reviewer rejection."""
    from engine_errors import infrastructure_error
    active=None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rsi_rounds'").fetchone():
        active=set()
        for row in db.execute("SELECT snapshot_json FROM rsi_rounds WHERE cycle_id=? AND status IN ('planned','running')",(cycle_id,)):
            active.update(json.loads(row[0]).get('identities',[]))
    rows = db.execute('''SELECT d.*,r.error AS engine_error FROM corpus_completion_documents d
        JOIN strategic_workflow_runs r ON r.id=d.workflow_run_id
        WHERE d.cycle_id=? AND d.status IN ('failed','running') AND r.status='failed'
        AND NOT EXISTS (SELECT 1 FROM corpus_engine_recoveries h
            WHERE h.cycle_id=d.cycle_id AND h.document_id=d.document_id)''', (cycle_id,)).fetchall()
    count = 0
    for row in rows:
        # A crashed ordinary RSI owner may have closed its round without updating
        # this ledger. Never reset a document still owned by an active checkpoint.
        if row['status']=='running' and (active is None or row['document_id'] in active):
            continue
        code = infrastructure_error(row['engine_error'])
        if not code:
            continue
        db.execute('INSERT INTO corpus_engine_recoveries VALUES (?,?,?,?)',
                   (cycle_id,row['document_id'],encoded(dict(row)),now()))
        admission = json.loads(row['admission_json'])
        admission.update(engine_recovery=code, prior_attempts=row['attempts'])
        db.execute("""UPDATE corpus_completion_documents SET status='pending',attempts=0,
            admission_json=?,updated_at=? WHERE cycle_id=? AND document_id=?""",
            (encoded(admission),now(),cycle_id,row['document_id']))
        count += 1
    return count


def historical_feedback(db,items):
    """Actual prior critiques on the same message snapshot; never reuse their verdict."""
    from completion_quality import message_text
    from link_groups import canonical_url
    targets={}
    for item in items:
        key=(canonical_url(item.get('source_url') or ''),message_text(item))
        targets.setdefault(key,[]).append(news_identity(item))
    output={}
    rows=db.execute("""SELECT r.id,r.error,r.snapshot_json,a.payload_json FROM strategic_workflow_runs r
        LEFT JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final'
        WHERE r.status IN ('needs_review','failed') ORDER BY r.updated_at DESC,r.id DESC""").fetchall()
    for row in rows:
        try:
            snapshot=json.loads(row['snapshot_json'])
            payload=json.loads(row['payload_json']) if row['payload_json'] else {}
        except (TypeError,ValueError):continue
        issues=[]
        for name in ('verification','risk_verification'):
            issues.extend(issue for issue in (payload.get(name) or {}).get('issues',[]) if isinstance(issue,str) and issue.strip())
        if row['error']:issues.append(row['error'])
        issues=list(dict.fromkeys(issues))
        if not issues:continue
        for item in snapshot:
            key=(canonical_url(item.get('url') or ''),str(item.get('text') or ''))
            for identity in targets.get(key,[]):
                if identity in output:continue
                output[identity]={'issues':issues,'feedback_sources':[{'workflow_run_id':row['id'],
                    'snapshot_evidence_id':item.get('id'),'message_snapshot_matched':True,
                    'scope':'이전 선택 범위의 실제 지적입니다. 현재 문서에 적용되는 내용은 현재 근거로 다시 대조해야 합니다.'}],
                    'complete':False,'verified':False,'status':'pending'}
    return output


class CompletionRunner:
    def __init__(self, path, cycle_id, workers=6, batch_size=24, recover_engine=False, review_first=False, recent_first=False, max_rounds=0, window_seconds=0, adaptive_workers=False):
        self.path = str(Path(path).resolve()); self.cycle_id = cycle_id
        self.workers = max(1, min(20, workers)); self.batch_size = max(1, min(24, batch_size))
        self.stop = threading.Event()
        self.ledger = None
        self.claimed_rounds = set()
        self.review_first = review_first
        self.recover_engine = recover_engine
        self.engine_error = ''
        self.recent_first = recent_first
        self.max_rounds = max(0, int(max_rounds))
        self.user_stopped = False
        self.window_seconds = max(0, window_seconds)
        self.window_expired = False
        self.handoff = False
        from completion_throughput import AdaptiveAdmission
        self.admission = AdaptiveAdmission(self.workers, adaptive_workers)

    def prepare(self):
        from improvement_selection import all_corpus_items
        with connect(self.path) as db:
            init(db)
            cycle = db.execute('SELECT * FROM rsi_cycles WHERE id=?', (self.cycle_id,)).fetchone()
            recoverable=bool(cycle and cycle['status'] in ('running','finishing','waiting') and not owner_alive(cycle['owner_pid']))
            if not cycle or (cycle['status'] not in ('paused', 'error', 'complete', 'needs_review') and not recoverable):
                raise RuntimeError('먼저 기존 RSI 분석을 체크포인트까지 일시중지해야 합니다.')
            if self.recover_engine:
                restored = recover_engine_failures(db, self.cycle_id)
                print(json.dumps({'engine_failures_requeued':restored}), flush=True)
                db.commit()
            existing = db.execute('SELECT COUNT(*) FROM corpus_completion_documents WHERE cycle_id=?', (self.cycle_id,)).fetchone()[0]
            if not existing:
                corpus = all_corpus_items(db)
                rows = db.execute('''SELECT r.id,r.status,r.error,a.payload_json FROM strategic_workflow_runs r
                    JOIN strategic_workflow_artifacts a ON a.run_id=r.id AND a.stage='final'
                    WHERE r.status='complete' ORDER BY r.updated_at DESC''').fetchall()
                candidates = {}
                for row in rows:
                    run = dict(row); run['results'] = json.loads(run.pop('payload_json'))
                    for e in run['results'].get('evidence', []):
                        if e.get('origin') == 'telegram_excerpt':
                            candidates.setdefault(e.get('url') or e.get('text'), []).append(run)
                state, seen = json.loads(cycle['state_json']), json.loads(cycle['seen_json'])
                feedback=historical_feedback(db,corpus)
                for position, item in enumerate(corpus):
                    identity = news_identity(item)
                    from completion_quality import message_text
                    admission = {}
                    for run in candidates.get(item.get('source_url') or message_text(item), []):
                        checked = document_admission(run, item)
                        if checked['complete']:
                            admission = checked; break
                    reused=bool(admission)
                    status = 'complete' if reused else 'pending'
                    if not reused:admission=feedback.get(identity,{})
                    db.execute('INSERT INTO corpus_completion_documents VALUES (?,?,?,?,?,0,?,?,?)',
                        (self.cycle_id, identity, position, encoded(item), status,
                         admission.get('workflow_run_id'), encoded(admission), now()))
                    if reused:
                        state[identity] = dict(admission, reused=True, risk_attempted=True)
                        seen[identity] = news_fingerprint(item)
                    elif state.get(identity,{}).get('status')=='verified':
                        state.pop(identity,None);seen.pop(identity,None)
                coverage = dict(corpus.coverage, corpus_ids=[news_identity(i) for i in corpus])
                db.execute('UPDATE rsi_cycles SET coverage_json=?,state_json=?,seen_json=? WHERE id=?',
                           (encoded(coverage), encoded(state), encoded(seen), self.cycle_id))
            if existing:
                # Reconcile old admission decisions against current validators without
                # rewriting any historical workflow report or source snapshot.
                from source_enrichment import attach_sources
                from risk_analysis import validate_risk_report
                corpus=all_corpus_items(db)
                current={news_identity(item):item for item in corpus}
                state,seen=json.loads(cycle['state_json']),json.loads(cycle['seen_json'])
                known={row[0] for row in db.execute('SELECT document_id FROM corpus_completion_documents WHERE cycle_id=?',(self.cycle_id,))}
                db.execute('''CREATE TABLE IF NOT EXISTS completion_input_history (
                    seq INTEGER PRIMARY KEY,cycle_id TEXT,document_id TEXT,prior_json TEXT,changed_at TEXT)''')
                # An input revision is a new analysis target. Preserve the entire
                # previous row; never reset attempts for an unchanged rejection.
                active_ids=set()
                for r in db.execute("SELECT snapshot_json FROM rsi_rounds WHERE cycle_id=? AND status IN ('planned','running')",(self.cycle_id,)):
                    active_ids.update(json.loads(r[0]).get('identities',[]))
                for old in db.execute('SELECT * FROM corpus_completion_documents WHERE cycle_id=?',(self.cycle_id,)).fetchall():
                    item=current.get(old['document_id'])
                    if not item or old['document_id'] in active_ids:continue
                    if news_fingerprint(item)==news_fingerprint(json.loads(old['snapshot_json'])):continue
                    db.execute('INSERT INTO completion_input_history(cycle_id,document_id,prior_json,changed_at) VALUES(?,?,?,?)',
                               (self.cycle_id,old['document_id'],encoded(dict(old)),now()))
                    db.execute("UPDATE corpus_completion_documents SET snapshot_json=?,status='pending',attempts=0,workflow_run_id=NULL,admission_json=?,updated_at=? WHERE cycle_id=? AND document_id=?",
                               (encoded(item),encoded({'reason':'changed_input','prior_attempts':old['attempts']}),now(),self.cycle_id,old['document_id']))
                    state.pop(old['document_id'],None);seen.pop(old['document_id'],None)
                position=db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM corpus_completion_documents WHERE cycle_id=?',(self.cycle_id,)).fetchone()[0]
                for identity,item in current.items():
                    if identity in known:continue
                    db.execute('INSERT INTO corpus_completion_documents VALUES (?,?,?,?,?,0,NULL,?,?)',
                        (self.cycle_id,identity,position,encoded(item),'pending',encoded({'reason':'new_corpus_document'}),now()))
                    position+=1
                coverage=dict(corpus.coverage,corpus_ids=list(current))
                db.execute('UPDATE rsi_cycles SET coverage_json=? WHERE id=?',(encoded(coverage),self.cycle_id))
                db.commit()

                from completion_reconciliation import AdmissionChecks, report_summaries
                checks=AdmissionChecks(db)
                runs={};stored_runs=report_summaries(db,self.cycle_id);reasons={};reconciled=[]
                completed=db.execute("SELECT * FROM corpus_completion_documents WHERE cycle_id=? AND status='complete'",(self.cycle_id,)).fetchall()
                frozen=attach_sources(db,[json.loads(row['snapshot_json']) for row in completed])
                for row,fallback in zip(completed,frozen):
                    run_id=row['workflow_run_id']
                    stored=stored_runs.get(run_id)
                    item=current.get(row['document_id'],fallback)
                    unchanged,check_key=checks.unchanged(self.cycle_id,row,stored,item)
                    if unchanged:continue
                    if run_id not in runs:
                        run=dict(stored) if stored else {'id':run_id,'status':'needs_review','error':'기존 분석 결과 없음'}
                        payload=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(run_id,)).fetchone()
                        run['results']=json.loads(payload[0] if payload else '{}');runs[run_id]=run
                        try:validate_risk_report(run['results'].get('risk_report'),run['results'].get('evidence') or [])
                        except (ValueError,TypeError,KeyError) as exc:reasons[run_id]=str(exc)
                    admission=document_admission(runs[run_id],item)
                    if admission['complete']:
                        if run_id not in reasons:checks.accept(self.cycle_id,row,check_key)
                        continue
                    reason=reasons.get(run_id)
                    if reason:admission['issues']=list(dict.fromkeys(admission['issues']+[reason]))
                    admission.update(status='needs_review',reconciliation_version='current-admission-v2',prior_attempts=row['attempts'])
                    # One bounded repair remains even if the old successful document
                    # exhausted its previous attempt budget. Failed rows are not reset.
                    reconciled.append((min(row['attempts'],2),encoded(admission),now(),self.cycle_id,row['document_id']))
                    state[row['document_id']]=dict(admission,risk_attempted=True)
                    seen.pop(row['document_id'],None)
                # Validating thousands of stored reports is CPU work. Only hold
                # SQLite's writer lock while applying the resulting decisions.
                checks.flush()
                print(json.dumps({'admission_checks_reused':checks.hits,'admission_checks_executed':checks.misses}),flush=True)
                db.executemany("UPDATE corpus_completion_documents SET status='needs_review',attempts=?,admission_json=?,updated_at=? WHERE cycle_id=? AND document_id=?",reconciled)
                db.execute('UPDATE rsi_cycles SET state_json=?,seen_json=? WHERE id=?',(encoded(state),encoded(seen),self.cycle_id))
            # Recover only this runner's unfinished jobs after its previous owner exited.
            # Running rows retain their original round and attempt. plan/work resume
            # the persisted workflow, including completed-but-not-recorded results.
            condition = ' AND pause_requested=0' if self.handoff else ''
            updated = db.execute("UPDATE rsi_cycles SET status='running',pause_requested=0,owner_pid=?,error='',updated_at=? WHERE id=?" + condition,
                       (os.getpid(), now(), self.cycle_id))
            if not updated.rowcount:
                raise RuntimeError('입력 갱신 중 요청된 일시중지를 보존합니다.')


    def cycle_control(self):
        with connect(self.path) as db:
            return db.execute('SELECT pause_requested FROM rsi_cycles WHERE id=?', (self.cycle_id,)).fetchone()[0]

    def plan(self):
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            for pending in db.execute("SELECT * FROM rsi_rounds WHERE cycle_id=? AND status IN ('planned','running') ORDER BY number",(self.cycle_id,)).fetchall():
                deferred = db.execute('SELECT next_attempt_at FROM completion_engine_waits WHERE round_id=?', (pending['id'],)).fetchone()
                if deferred and deferred[0] > time.time():continue
                snapshot=json.loads(pending['snapshot_json'])
                if snapshot.get('items') and snapshot.get('identities') and pending['id'] not in self.claimed_rounds:
                    # Older web-owned rounds have no completion_attempt. Preserve
                    # their exact workflow/evidence checkpoint instead of orphaning it.
                    if not snapshot.get('completion_attempt'):
                        attempts = [db.execute('SELECT attempts FROM corpus_completion_documents WHERE cycle_id=? AND document_id=?',
                                              (self.cycle_id, identity)).fetchone() for identity in snapshot['identities']]
                        snapshot['completion_attempt'] = max([r[0] for r in attempts if r] + [1])
                    self.claimed_rounds.add(pending['id'])
                    return {'id':pending['id'],'number':pending['number'],'snapshot':snapshot,'workflow_run_id':pending['workflow_run_id']}
            # Persisted round numbers preserve a 2:1 new/review preference across
            # restarts. Either queue falls back to the other when it is empty.
            number = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM rsi_rounds WHERE cycle_id=?', (self.cycle_id,)).fetchone()[0]
            prefer_review = self.review_first or number % 3 == 0
            order = ("CASE WHEN (attempts>0)=? THEN 0 ELSE 1 END,attempts,"
                     + ("json_extract(snapshot_json,'$.day') DESC," if self.recent_first and number % 4 else '')
                     + 'position')
            rows = db.execute('''SELECT * FROM corpus_completion_documents WHERE cycle_id=?
                AND status IN ('pending','needs_review','failed') AND attempts<3
                ORDER BY ''' + order + ' LIMIT ?',
                (self.cycle_id, int(prefer_review), self.batch_size)).fetchall()
            if not rows: return None
            attempt = rows[0]['attempts']
            count = self.batch_size if attempt == 0 else 6 if attempt == 1 else 1
            rows = [r for r in rows if r['attempts'] == attempt][:count]
            items = [json.loads(r['snapshot_json']) for r in rows]
            identities = [r['document_id'] for r in rows]
            issues = list(dict.fromkeys(issue for r in rows for issue in json.loads(r['admission_json']).get('issues', [])))
            round_id = uuid.uuid4().hex
            snapshot = {'items': items, 'identities': identities,
                        'fingerprints': {news_identity(i): news_fingerprint(i) for i in items},
                        'new_document_count': len(items) if attempt == 0 else 0,
                        'same_snapshot_retry': attempt > 0, 'completion_attempt': attempt + 1,
                        'selection_policy':'review_first_v1' if self.review_first else 'new_new_review_v1','preferred_queue':'review' if prefer_review else 'new',
                        'improvement_context': {'review_issues': issues,
                            'followup_tasks':[{'kind':'completion_review','text':'\n'.join(issues),'search_terms':[str(item.get('title') or item.get('text') or '')[:100] for item in items]}] if issues else [],
                                               'instruction': '이전 지적은 보완 질문이며 새로운 사실 근거가 아니다.'}}
            db.execute('''INSERT INTO rsi_rounds(id,cycle_id,number,status,created_at,snapshot_json)
                          VALUES (?,?,?,'planned',?,?)''', (round_id, self.cycle_id, number, now(), encoded(snapshot)))
            for identity in identities:
                db.execute("UPDATE corpus_completion_documents SET status='running',attempts=attempts+1,updated_at=? WHERE cycle_id=? AND document_id=?",
                           (now(), self.cycle_id, identity))
            self.claimed_rounds.add(round_id)
            return {'id': round_id, 'number': number, 'snapshot': snapshot}

    def work(self, service, planned):
        snapshot = planned['snapshot']
        request = {'full_corpus': True, 'completion': True, 'recursive_cycle_id': self.cycle_id,
                   'recursive_round_id': planned['id'], 'improvement_context': snapshot['improvement_context'],
                   'completion_attempt': snapshot.get('completion_attempt',1), 'analysis_mode': 'adaptive-v2'}
        with connect(self.path) as db:
            prior=db.execute("SELECT id FROM strategic_workflow_runs WHERE json_extract(request_json,'$.recursive_round_id')=? ORDER BY created_at DESC LIMIT 1",(planned['id'],)).fetchone()
        prior_id=planned.get('workflow_run_id') or (prior[0] if prior else None)
        run=service.get_run(prior_id) if prior_id else None
        if run and run['status'] in ('queued','running'):
            with connect(self.path) as db:
                row=db.execute('SELECT request_json FROM strategic_workflow_runs WHERE id=?',(run['id'],)).fetchone()
                previous_owner=json.loads(row[0]).get('owner_pid')
                if owner_alive(previous_owner):raise RuntimeError('기존 워크플로 소유자가 살아 있어 중복 재개할 수 없습니다.')
                db.execute("UPDATE strategic_workflow_runs SET status='paused',updated_at=? WHERE id=?",(now(),run['id']))
            run=service.get_run(run['id'])
        if run and run['status'] in ('paused','failed'):run=service.resume(run['id'])
        elif not run:run=service.create_run(snapshot['items'], request)
        with connect(self.path) as db:
            db.execute("UPDATE rsi_rounds SET status='running',workflow_run_id=? WHERE id=?", (run['id'], planned['id']))
        while service.active:
            time.sleep(1)
        result=service.get_run(run['id'])
        if result and result['status'] in ('queued','running'):
            # A worker may fail even to persist its error during a DB outage.
            # Do not mistake that nonterminal row for a rejected content review.
            from engine_errors import EngineError
            result=dict(result,status='failed',error=str(EngineError('execution_failed')))
        return planned, result

    def record(self, planned, run):
        from engine_errors import infrastructure_error
        code = infrastructure_error(run.get('error')) if run.get('status')=='failed' else None
        self.admission.observe(code)
        if code:
            # Keep the same round/workflow checkpoints. An outage is not a failed
            # content review and must not consume thousands of document attempts.
            with connect(self.path) as db:
                db.execute("UPDATE rsi_rounds SET status='planned',error=? WHERE id=?",
                           (run.get('error',''),planned['id']))
                db.execute('INSERT INTO completion_engine_events(cycle_id,round_id,error,created_at) VALUES(?,?,?,?)',
                           (self.cycle_id,planned['id'],run.get('error',''),now()))
                if code in {'capacity','timeout','queue_timeout','network','database_locked'}:
                    old = db.execute('SELECT failures FROM completion_engine_waits WHERE round_id=?', (planned['id'],)).fetchone()
                    failures = (old[0] if old else 0) + 1
                    delay = min(900, 30 * 2 ** min(failures - 1, 5))
                    db.execute('INSERT OR REPLACE INTO completion_engine_waits VALUES(?,?,?,?,?)',
                               (planned['id'],failures,time.time()+delay,run.get('error',''),now()))
                    self.claimed_rounds.discard(planned['id'])
                    streak = 0
                    for event in db.execute('SELECT error FROM completion_engine_events WHERE cycle_id=? ORDER BY seq DESC LIMIT 3', (self.cycle_id,)):
                        if not event[0]:break
                        streak += 1
                    if streak < 3:
                        print(json.dumps({'round':planned['number'],'status':'engine_waiting','error_code':code,'retry_after_seconds':delay}),flush=True)
                        return
            self.stop.set()
            self.engine_error = run.get('error','')
            print(json.dumps({'round':planned['number'],'status':'engine_paused','error_code':code}),flush=True)
            return
        from improvement_memory import improve_catalog
        from source_enrichment import attach_sources
        snapshot = planned['snapshot']
        quality = quality_metrics(run)
        with connect(self.path) as db:
            items = attach_sources(db, snapshot['items'])
        admissions = [document_admission(run, item) for item in items]
        # Genuine model feedback is kept in the existing recursive review memory.
        self.ledger._feedback(self.cycle_id, run, quality)
        with connect(self.path) as db:
            catalog = improve_catalog(db, run['id'], run)
            for task in catalog.get('followup_tasks', []):
                self.ledger._task(db, self.cycle_id, run['id'], task.get('kind', 'followup'),
                                  task.get('reason') or task.get('title') or '',
                                  task.get('evidence_ids', []), task.get('search_terms', []))
        with connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            cycle = db.execute('SELECT state_json,seen_json FROM rsi_cycles WHERE id=?', (self.cycle_id,)).fetchone()
            state, seen = json.loads(cycle[0]), json.loads(cycle[1])
            for item, admission in zip(items, admissions):
                identity = news_identity(item)
                state[identity] = dict(admission, reused=False, risk_attempted=bool((run.get('results') or {}).get('risk_report')))
                seen[identity] = news_fingerprint(item)
                db.execute('''UPDATE corpus_completion_documents SET status=?,workflow_run_id=?,admission_json=?,snapshot_json=?,updated_at=?
                    WHERE cycle_id=? AND document_id=?''',
                    ('complete' if admission['complete'] else admission['status'] if not admission['verified'] else 'needs_review',
                     run['id'], encoded(admission), encoded(item), now(), self.cycle_id, identity))
            round_status = 'complete' if all(a['complete'] for a in admissions) else 'failed' if run['status'] == 'failed' else 'needs_review'
            db.execute('''UPDATE rsi_rounds SET status=?,completed_at=?,after_json=?,comparison_json=?,catalog_json=?,error=? WHERE id=?''',
                (round_status, now(), encoded(quality), encoded({'assessment': '문서별 인용과 위험 독립 검토를 별도로 검사했습니다.',
                 'documents': len(items), 'verified_documents': sum(a['complete'] for a in admissions)}),
                 encoded({k: v for k, v in catalog.items() if k not in ('added', 'updated')}), run.get('error', ''), planned['id']))
            db.execute('DELETE FROM completion_engine_waits WHERE round_id=?', (planned['id'],))
            db.execute('INSERT INTO completion_engine_events(cycle_id,round_id,error,created_at) VALUES(?,?,?,?)',
                       (self.cycle_id,planned['id'],'',now()))
            db.execute('UPDATE rsi_cycles SET state_json=?,seen_json=?,updated_at=? WHERE id=?',
                       (encoded(state), encoded(seen), now(), self.cycle_id))
        print(json.dumps({'round': planned['number'], 'documents': len(items),
                          'accepted': sum(a['complete'] for a in admissions), 'status': round_status}, ensure_ascii=False), flush=True)

    def summary(self):
        with connect(self.path) as db:
            counts = dict(db.execute('SELECT status,COUNT(*) FROM corpus_completion_documents WHERE cycle_id=? GROUP BY status', (self.cycle_id,)))
            return {'counts': counts, 'total': sum(counts.values()), 'updated_at': now()}

    def run(self):
        with open(self.path + '.completion.lock', 'a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._run_locked()

    def _run_locked(self):
        from strategic_workflow import WorkflowService
        from source_enrichment import SourceService
        from recursive_improvement import RecursiveImprovementService
        self.prepare()
        sources = SourceService(self.path)
        services = [WorkflowService(self.path, sources=sources, enabled=True, recover_interrupted=False) for _ in range(self.workers)]
        self.ledger = RecursiveImprovementService(self.path, services[0], lambda *_: [])
        old_handlers={}
        def signal_stop(*_):
            self.user_stopped=True
            self.stop.set()
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                old_handlers[sig]=signal.signal(sig,signal_stop)
        failed = None
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {}; available = list(services); dispatched=0
                started = time.monotonic()
                while True:
                    if self.cycle_control(): self.stop.set()
                    self.window_expired = bool(self.window_seconds and time.monotonic() - started >= self.window_seconds)
                    while available and not self.stop.is_set() and not self.window_expired and len(futures) < self.admission.target:
                        if self.max_rounds and dispatched>=self.max_rounds:break
                        planned = self.plan()
                        if not planned: break
                        dispatched+=1
                        service = available.pop()
                        futures[pool.submit(self.work, service, planned)] = service
                    if not futures: break
                    finished, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
                    for future in finished:
                        service = futures.pop(future)
                        planned, result = future.result()
                        prior_target = self.admission.target
                        self.record(planned, result)
                        if prior_target != self.admission.target:
                            print(json.dumps({'status':'admission_adjusted','workers_max':self.workers,'workers_target':self.admission.target}),flush=True)
                        available.append(service)
        except Exception as exc:
            failed = type(exc).__name__ + ': ' + str(exc)[:300]
            raise
        finally:
            for service in services: service.close()
            sources.close()
            summary = self.summary()
            complete = summary['counts'].get('complete', 0) == summary['total'] and summary['total'] > 0
            status = 'error' if failed else 'paused' if self.stop.is_set() else 'complete' if complete else 'needs_review'
            if (self.max_rounds or self.window_expired) and not failed and not self.stop.is_set() and not complete:
                with connect(self.path) as db:
                    if db.execute("SELECT 1 FROM corpus_completion_documents WHERE cycle_id=? AND status IN ('pending','needs_review','failed','running') AND attempts<3 LIMIT 1",(self.cycle_id,)).fetchone():status='waiting'
            if not failed and not self.stop.is_set() and not complete:
                with connect(self.path) as db:
                    if db.execute("SELECT 1 FROM rsi_rounds WHERE cycle_id=? AND status IN ('planned','running') LIMIT 1", (self.cycle_id,)).fetchone():status='waiting'
            with connect(self.path) as db:
                user_pause = self.user_stopped or bool(db.execute('SELECT pause_requested FROM rsi_cycles WHERE id=?',(self.cycle_id,)).fetchone()[0])
                db.execute('UPDATE rsi_cycles SET status=?,owner_pid=NULL,pause_requested=?,error=?,updated_at=? WHERE id=?',
                           (status, int(user_pause), failed or self.engine_error, now(), self.cycle_id))
            print(json.dumps(dict(summary, status=status), ensure_ascii=False), flush=True)
            for sig,handler in old_handlers.items():signal.signal(sig,handler)
        return dict(summary, status=status)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='data/news.sqlite3')
    parser.add_argument('--cycle', required=True)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--batch-size', type=int, default=24)
    parser.add_argument('--recover-engine-failures', action='store_true')
    parser.add_argument('--review-first', action='store_true')
    parser.add_argument('--recent-first', action='store_true')
    parser.add_argument('--max-rounds', type=int, default=0)
    parser.add_argument('--window-seconds', type=int, default=0)
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--adaptive-workers', action='store_true')
    args = parser.parse_args()
    if args.continuous and (args.window_seconds <= 0 or args.max_rounds):
        parser.error('--continuous requires --window-seconds > 0 and --max-rounds 0')
    from app import load_local_env
    load_local_env()
    admission = None
    while True:
        runner = CompletionRunner(args.db, args.cycle, args.workers, args.batch_size, args.recover_engine_failures, args.review_first, args.recent_first, args.max_rounds, args.window_seconds, args.adaptive_workers)
        if admission is not None:
            runner.admission = admission
            runner.handoff = True
        result = runner.run()
        admission = runner.admission
        if not (args.continuous and runner.window_expired and result['status'] == 'waiting'):
            break
        # An operator pause during handoff must not be cleared by prepare().
        if runner.cycle_control(): break
