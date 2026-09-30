"""Process-shared model admission and content-free execution telemetry."""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import sqlite3
import time

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'llm_runtime.sqlite3'
DEFAULT_LIMIT = 6
INTERACTIVE_ROLES = ('graph_answer', 'strategic_question', 'strategic_question_verification', 'engine_probe')


class ClosingConnection(sqlite3.Connection):
    def __enter__(self):
        self._context_depth = getattr(self, '_context_depth', 0) + 1
        return super().__enter__()

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self._context_depth = getattr(self, '_context_depth', 1) - 1
            if self._context_depth == 0:
                self.close()


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def infer_role(prompt, schema):
    match = re.match(r'^ROLE:\s*([a-z_]{1,48})\s*(?:\n|$)', str(prompt))
    if match:
        return match[1]
    fields = set(schema.get('properties', {})) if isinstance(schema, dict) else set()
    if 'analyses' in fields:
        return 'research_documents'
    if 'covered_doc_ids' in fields:
        return 'research_synthesis'
    if 'answer' in fields:
        return 'graph_answer'
    return 'structured_analysis'


class _Ticket:
    def __init__(self, identity):
        self.id = identity
        self.error_code = ''
        self.output_chars = 0


class LLMRuntime:
    def __init__(self, path=None, limit=None, poll_seconds=.2, queue_timeout=900):
        self.path = str(path or os.environ.get('NEWS_LLM_RUNTIME_DB') or DEFAULT_PATH)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.poll_seconds = poll_seconds
        self.queue_timeout = queue_timeout
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS llm_runtime_settings(id INTEGER PRIMARY KEY CHECK(id=1),max_concurrent INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO llm_runtime_settings VALUES (1,?)', (DEFAULT_LIMIT if limit is None else max(1, min(int(limit), 12)),))
            db.execute('''CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,role TEXT NOT NULL,status TEXT NOT NULL,
                owner_pid INTEGER NOT NULL,queued_at REAL NOT NULL,started_at REAL,finished_at REAL,
                lease_until REAL,input_chars INTEGER NOT NULL,schema_chars INTEGER NOT NULL,
                output_chars INTEGER NOT NULL DEFAULT 0,wait_ms INTEGER,run_ms INTEGER,error_code TEXT NOT NULL DEFAULT '')''')
            db.execute('CREATE INDEX IF NOT EXISTS llm_calls_active ON llm_calls(status,id)')
            columns = {r[1] for r in db.execute('PRAGMA table_info(llm_calls)')}
            for column in ('model', 'reasoning_effort'):
                if column not in columns:
                    db.execute(f"ALTER TABLE llm_calls ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    def db(self):
        db = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _reap(db, stamp):
        rows = db.execute("SELECT id,owner_pid,status,lease_until FROM llm_calls WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            expired = row['status'] == 'running' and row['lease_until'] is not None and row['lease_until'] < stamp
            alive = _alive(row['owner_pid'])
            if not alive and row['status'] == 'running' and row['lease_until'] and not expired:
                # The CLI child may outlive its owner. Reserve its slot until the
                # bounded execution lease expires rather than immediately overbook.
                db.execute("UPDATE llm_calls SET error_code='owner_exited_waiting_lease' WHERE id=?", (row['id'],))
            elif not alive:
                db.execute("UPDATE llm_calls SET status='failed',finished_at=?,error_code=? WHERE id=?",
                           (stamp, 'owner_exited', row['id']))
            elif expired:
                # An overdue live caller still owns a slot: never exceed the budget
                # merely because its subprocess or disk cleanup is slow.
                db.execute("UPDATE llm_calls SET error_code='lease_overdue' WHERE id=?", (row['id'],))

    @staticmethod
    def _outage(db, stamp):
        """Back off only a recent uninterrupted series of engine failures."""
        from engine_errors import INFRASTRUCTURE_CODES
        rows = db.execute("""SELECT status,error_code,finished_at FROM llm_calls
            WHERE status IN ('complete','failed') AND finished_at>? AND error_code NOT IN
            ('circuit_open','queue_timeout','owner_exited','owner_exited_waiting_lease')
            ORDER BY finished_at DESC,id DESC LIMIT 3""", (stamp-120,)).fetchall()
        return len(rows)==3 and all(row['status']=='failed' and row['error_code'] in INFRASTRUCTURE_CODES for row in rows)

    @contextmanager
    def slot(self, role, input_chars, schema_chars, max_run_seconds=240, model='', reasoning_effort=''):
        if not re.fullmatch(r'[a-z_]{1,48}', role):
            role = 'structured_analysis'
        queued = time.time()
        with self.db() as db:
            identity = db.execute("INSERT INTO llm_calls(role,status,owner_pid,queued_at,input_chars,schema_chars,model,reasoning_effort) VALUES (?,'queued',?,?,?,?,?,?)",
                                  (role, os.getpid(), queued, int(input_chars), int(schema_chars), model, reasoning_effort)).lastrowid
        ticket = _Ticket(identity)
        started = None
        try:
            while started is None:
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    # Admission timestamps must follow lock acquisition. A timestamp
                    # captured before SQLite contention can falsely overlap a prior call.
                    stamp = time.time()
                    self._reap(db, stamp)
                    if role != 'engine_probe' and self._outage(db, stamp):
                        from engine_errors import EngineError
                        ticket.error_code = 'circuit_open'
                        raise EngineError('circuit_open')
                    limit = db.execute('SELECT max_concurrent FROM llm_runtime_settings WHERE id=1').fetchone()[0]
                    active = db.execute("SELECT COUNT(*) FROM llm_calls WHERE status='running'").fetchone()[0]
                    # Interactive questions wait for a free slot ahead of bulk jobs;
                    # old jobs age into that tier after 60 seconds to prevent starvation.
                    bulk_active = db.execute("SELECT COUNT(*) FROM llm_calls WHERE status='running' AND role NOT IN (?,?,?,?)",
                                             INTERACTIVE_ROLES).fetchone()[0]
                    analysis_active = db.execute("SELECT COUNT(*) FROM llm_calls WHERE status='running' AND role NOT LIKE '%verification' AND role NOT IN (?,?,?,?)", INTERACTIVE_ROLES).fetchone()[0]
                    waiting = db.execute("""SELECT id,role FROM llm_calls WHERE status='queued'
                        ORDER BY CASE WHEN role IN (?,?,?,?) OR queued_at<? THEN 0 ELSE 1 END,id""",
                        (*INTERACTIVE_ROLES, stamp-60)).fetchall()
                    eligible=[]
                    for pending in waiting:
                        if len(eligible)>=max(0,limit-active):break
                        if pending['role'] not in INTERACTIVE_ROLES:
                            # Retain one slot for an interactive answer while bulk
                            # analysis is busy. The total cross-process limit is unchanged.
                            if bulk_active>=max(1,limit-1):continue
                            if not pending['role'].endswith('verification'):
                                if analysis_active >= max(1, limit-2):continue
                                analysis_active += 1
                            bulk_active+=1
                        eligible.append(pending['id'])
                    if identity in eligible:
                        started = stamp
                        db.execute("UPDATE llm_calls SET status='running',started_at=?,lease_until=?,wait_ms=? WHERE id=?",
                                   (stamp, stamp+max_run_seconds+60, round((stamp-queued)*1000), identity))
                if started is None:
                    if stamp-queued >= self.queue_timeout:
                        ticket.error_code = 'queue_timeout'
                        raise RuntimeError('모델 실행 대기 시간이 초과되었습니다. 현재 실행이 끝난 뒤 재개해 주세요.')
                    time.sleep(self.poll_seconds)
            yield ticket
        except BaseException:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                stamp = time.time()
                db.execute("UPDATE llm_calls SET status='failed',finished_at=?,wait_ms=COALESCE(wait_ms,?),run_ms=?,output_chars=?,error_code=? WHERE id=?",
                           (stamp, round(((started or stamp)-queued)*1000), round((stamp-started)*1000) if started else 0,
                            ticket.output_chars, ticket.error_code or 'request_failed', identity))
            raise
        else:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                stamp = time.time()
                db.execute("UPDATE llm_calls SET status='complete',finished_at=?,run_ms=?,output_chars=? WHERE id=?",
                           (stamp, round((stamp-started)*1000), ticket.output_chars, identity))


def runtime_status(path=None):
    runtime = LLMRuntime(path)
    with runtime.db() as db:
        runtime._reap(db, time.time())
        limit = db.execute('SELECT max_concurrent FROM llm_runtime_settings WHERE id=1').fetchone()[0]
        counts = dict(db.execute('SELECT status,COUNT(*) FROM llm_calls GROUP BY status'))
        roles = [dict(row) for row in db.execute('''SELECT role,COUNT(*) calls,
            SUM(status='complete') completed,SUM(status='failed') failed,
            ROUND(AVG(wait_ms)) mean_wait_ms,ROUND(AVG(run_ms)) mean_run_ms,
            ROUND(AVG(input_chars)) mean_input_chars,ROUND(AVG(output_chars)) mean_output_chars
            FROM llm_calls GROUP BY role ORDER BY calls DESC''')]
        recent = [dict(row) for row in db.execute('''SELECT id,role,status,queued_at,started_at,finished_at,input_chars,schema_chars,
            output_chars,wait_ms,run_ms,error_code,model,reasoning_effort FROM llm_calls ORDER BY id DESC LIMIT 40''')]
    return {'limit': limit, 'active': counts.get('running', 0), 'waiting': counts.get('queued', 0),
            'counts': counts, 'roles': roles, 'recent': recent, 'scope': 'all_local_processes_using_shared_runtime',
            'privacy': '길이·시간·역할·모델·오류코드를 기록하며 프롬프트·결과 본문·인증정보는 저장하지 않습니다.'}
