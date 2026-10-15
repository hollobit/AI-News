"""Reuse a question's search plan; it contains queries, never evidence or conclusions."""
from datetime import date
import json
import sqlite3
import re

from projection_cache import content_digest

VERSION = 'question-plan-v2'


def simple_plan(question):
    """Only explicit single-topic news lookups; compound questions keep planning."""
    match=re.fullmatch(r'\s*(?:최근\s+)?([A-Za-z0-9가-힣+.\- ]{2,45}?)\s+(?:관련\s+)?뉴스(?:\s*(?:현황|소식))?\s*(?:알려\s*줘|알려\s*주세요|보여\s*줘|보여\s*주세요)?\s*[?.]?\s*',question)
    if not match:return None
    subject=match[1].strip()
    if re.search(r'및|그리고|비교|차이|(?:와|과)\s',subject) or len(subject.split())>4:return None
    return {'queries':[subject,subject+' 상충 근거 관계 시사점'],'axes':[question.strip()]}


def valid(plan):
    return isinstance(plan, dict) and all(isinstance(plan.get(k), list) and 1 <= len(plan[k]) <= 4
        and all(isinstance(v, str) and v.strip() and len(v) <= 300 for v in plan[k]) for k in ('queries', 'axes'))


def prepare(question, path=None):
    from semantic import run_structured
    from strategic_jobs import obj
    key = content_digest([VERSION, date.today().isoformat(), question])
    if path:
        with sqlite3.connect(path, timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS graph_question_plans(key TEXT PRIMARY KEY,plan_json TEXT NOT NULL,created_at TEXT NOT NULL)')
            row = db.execute('SELECT plan_json FROM graph_question_plans WHERE key=?', (key,)).fetchone()
            if row:
                plan = json.loads(row[0])
                if valid(plan): return plan, True
    plan = simple_plan(question) or run_structured('질문을 분석할 검색 계획만 작성한다. 사실에 답하거나 전제를 새로 만들지 않는다. '
        '질문에 명시된 대상과 비교 기준을 빠짐없이 나누어 최대 4개의 짧은 검색어를 queries에 적는다. '
        '등록 표현과 다른 한국어·영어 표현도 검색어로 사용할 수 있다. 필요한 반대 근거도 포함한다. '
        'axes에는 실제 질문의 비교 기준을 적는다. 통합 분석에서는 개별 사실뿐 아니라 자료간 공통점·상충 근거·의존 관계와 시사점을 찾을 검색어도 포함한다.\n질문: ' + question,
        obj({'queries': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {'type': 'string'}},
             'axes': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {'type': 'string'}}}),
        role='graph_answer', timeout=45, queue_timeout=15, reasoning_effort='low')
    if not valid(plan): raise RuntimeError('질문별 검색 계획 형식 오류')
    if path:
        with sqlite3.connect(path, timeout=10) as db:
            db.execute('INSERT OR REPLACE INTO graph_question_plans VALUES(?,?,?)', (key, json.dumps(plan, ensure_ascii=False), date.today().isoformat()))
            db.execute('DELETE FROM graph_question_plans WHERE key NOT IN (SELECT key FROM graph_question_plans ORDER BY created_at DESC,rowid DESC LIMIT 128)')
    return plan, False
