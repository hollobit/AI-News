"""Question orchestration must use spare slots without bypassing admission."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

from graph_questions import GraphQuestions
from llm_runtime import LLMRuntime


class Snapshots:
    def request(self, params): return 'revision', object(), None
    def key(self, params): return 'revision'
    def close(self): pass


def until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.01)
    assert predicate()


def service(tmp_path, monkeypatch, analyzer=None):
    path = tmp_path / 'questions.db'
    with sqlite3.connect(path) as db: db.execute('PRAGMA journal_mode=WAL')
    preview = lambda *args: dict(evidence=[{'id': 'source'}], limitations=[])
    monkeypatch.setattr('graph_questions.quick_answer', preview)
    monkeypatch.setattr('graph_questions.cold_answer', preview)
    return GraphQuestions(path, snapshots=Snapshots(), analyzer=analyzer)


def test_four_questions_start_and_queue_stays_bounded(tmp_path, monkeypatch):
    gate = threading.Event()
    entered = []

    def analyze(graph, question, ids):
        entered.append(question)
        assert gate.wait(5)
        return dict(answer=question, evidence=[{'id': 'source'}])

    questions = service(tmp_path, monkeypatch, analyze)
    try:
        answers = [questions.ask(f'서로 다른 질문 {i}') for i in range(8)]
        until(lambda: len(entered) == 4)
        with questions.db() as db:
            assert db.execute("SELECT count(*) FROM graph_question_jobs WHERE status='running'").fetchone()[0] == 4
            assert db.execute("SELECT count(*) FROM graph_question_jobs WHERE status='queued'").fetchone()[0] == 4
        # A repeat shares its queued job even when the queue is full.
        duplicate = questions.ask('서로 다른 질문 7')
        assert duplicate['job']['id'] == answers[7]['job']['id']
        assert questions.ask('아홉 번째 별도 질문')['analysis_status'] == 'busy'
        gate.set()
        until(lambda: not questions.active)
        assert Counter(entered) == Counter(f'서로 다른 질문 {i}' for i in range(8))
        assert all(questions.get(a['job']['id'])['status'] == 'complete' for a in answers)
    finally:
        gate.set()
        questions.close()
        questions.executor.shutdown(wait=True)


def test_shared_model_limit_still_applies_to_all_four_questions(tmp_path, monkeypatch):
    runtime = LLMRuntime(tmp_path / 'models.db', limit=2, poll_seconds=.01)
    gate = threading.Event()
    entered = []

    def analyze(graph, question, ids):
        entered.append(question)
        for role in ('graph_answer', 'strategic_question_verification'):
            with runtime.slot(role, 100, 10):
                assert gate.wait(5)
        return dict(answer=question, evidence=[{'id': 'source'}])

    questions = service(tmp_path, monkeypatch, analyze)
    def states():
        with runtime.db() as db:
            return dict(db.execute('SELECT status,count(*) FROM llm_calls GROUP BY status'))
    try:
        answers = [questions.ask(f'공유 한도 질문 {i}') for i in range(4)]
        until(lambda: len(entered) == 4 and states() == {'queued': 2, 'running': 2})
        gate.set()
        until(lambda: not questions.active)
        assert states() == {'complete': 8}
        assert all(questions.get(a['job']['id'])['status'] == 'complete' for a in answers)
        with runtime.db() as db:
            rows = db.execute('SELECT started_at,finished_at FROM llm_calls').fetchall()
            assert db.execute('SELECT max_concurrent FROM llm_runtime_settings').fetchone()[0] == 2
        events = sorted([(r[0], 1) for r in rows] + [(r[1], -1) for r in rows])
        active = peak = 0
        for _, delta in events:
            active += delta
            peak = max(peak, active)
        assert peak == 2 and active == 0
    finally:
        gate.set()
        questions.close()
        questions.executor.shutdown(wait=True)


def test_four_plans_progress_and_unreviewed_answers_stay_held(tmp_path, monkeypatch):
    gate = threading.Event()
    entered = []
    questions = service(tmp_path, monkeypatch)

    def plan(identity, question):
        entered.append(question)
        assert gate.wait(5)
        return dict(queries=[question], axes=['근거'])

    monkeypatch.setattr(questions, '_plan', plan)
    monkeypatch.setattr('graph_questions.deepen', lambda *args, **kwargs:
                        dict(answer='미검토 설명', claims=[{}], evidence=[{'id': 'source'}]))
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda i: questions.ask(f'독립 계획 질문 {i}', params={'workspace': ['1']}), range(4)))
        until(lambda: len(entered) == 4)
        gate.set()
        until(lambda: not questions.active)
        assert all(questions.get(a['job']['id'])['status'] == 'needs_review' for a in answers)
    finally:
        gate.set()
        questions.close()
        questions.executor.shutdown(wait=True)
        questions.planners.shutdown(wait=True)
