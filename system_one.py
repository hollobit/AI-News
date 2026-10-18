"""Bounded Jev decisions. Never approve or publish an analysis.

The provider returns closed choices, not explanations. Confidence is a model
distribution statistic, never an event probability or a quality guarantee.
"""
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request

VERSION = 'system-one-scoped-plan-v3'
ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
DEFAULT_MODEL = 'jev-1.14.0'
TIMEOUT = 2.0
PLAN_ADMISSION_SECONDS = .25
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='decision-shadow')
PENDING = threading.BoundedSemaphore(1)


def config():
    mode = os.getenv('NEWS_SYSTEM_ONE_MODE', 'shadow')
    model = os.getenv('NEWS_SYSTEM_ONE_MODEL', DEFAULT_MODEL)
    configured = bool(os.getenv('TYPESAFE_API_KEY'))
    # Pin calibration. Search plans apply by default; optional keyword cleanup
    # requires an explicit opt-in. Neither approves evidence or analysis.
    valid = mode in ('off', 'shadow') and bool(re.fullmatch(r'jev-\d+\.\d+\.\d+', model))
    enabled = valid and mode=='shadow' and configured
    plan_mode = os.getenv('NEWS_QUESTION_PLAN_MODE', 'jev')
    plans = enabled and plan_mode=='jev' and model==DEFAULT_MODEL
    keywords = enabled and os.getenv('NEWS_BASELINE_KEYWORDS_MODE','off')=='jev' and model==DEFAULT_MODEL
    drafts = enabled and os.getenv('NEWS_QUESTION_DRAFT_MODE','jev')=='jev' and model==DEFAULT_MODEL
    return dict(mode=mode if valid else 'invalid', model=model if valid else None,
                configured=configured, enabled=enabled, question_plan_mode=plan_mode if plan_mode in ('jev','off') else 'invalid',
                applies_decisions=plans or keywords or drafts, applies_question_plans=plans, applies_baseline_keywords=keywords,
                applies_question_drafts=drafts,
                applies_evidence_selection=False, applies_workflow_decisions=False, version=VERSION)


def db_path(path):
    return Path(str(path)+'.decisions.sqlite3')


def connect(path):
    db = sqlite3.connect(db_path(path), timeout=.2)
    db.row_factory = sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS decisions(
        key TEXT PRIMARY KEY,purpose TEXT,model TEXT,status TEXT,baseline TEXT,
        result TEXT,elapsed_ms REAL,created_at REAL);
        CREATE TABLE IF NOT EXISTS provider_wait(id INTEGER PRIMARY KEY,until REAL,reason TEXT);''')
    return db


def probability(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1


def validate(result, questions, model):
    if not isinstance(result,dict) or result.get('model')!=model:raise ValueError('model_mismatch')
    answers=result.get('answers')
    if not isinstance(answers,dict) or set(answers)!=set(questions):raise ValueError('missing_answers')
    for key,question in questions.items():
        answer=answers[key]
        if not isinstance(answer,dict) or answer.get('type')!=question['type']:raise ValueError('invalid_type')
        if question['type']=='noul':
            if not probability(answer.get('noul')):raise ValueError('invalid_noul')
            continue
        if question['type']!='choice':raise ValueError('unsupported_type')
        probabilities=answer.get('probabilities')
        if (not isinstance(probabilities,dict) or set(probabilities)!=set(question['criteria'])
            or not all(probability(p) for p in probabilities.values())
            or not math.isclose(sum(probabilities.values()),1,abs_tol=.01)
            or answer.get('choice') not in probabilities
            or probabilities[answer['choice']]<max(probabilities.values())-.001
            or not probability(answer.get('confidence'))):raise ValueError('invalid_choice')
    usage=result.get('usage') or {}
    return dict(model=model,answers=answers,usage={k:usage[k] for k in ('input_tokens','output_tokens')
                if type(usage.get(k)) is int and usage[k]>=0})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def transport(body):
    request=urllib.request.Request(ENDPOINT,data=body,headers={
        'Authorization':'Bearer '+os.environ['TYPESAFE_API_KEY'],'Content-Type':'application/json'})
    # Never forward the bearer token to a redirect target.
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=TIMEOUT) as response:
        raw=response.read(131073)
        if len(raw)>131072:raise ValueError('oversized_response')
        return json.loads(raw)


def retry_after(value, stamp):
    try:return max(1,float(value))
    except (ValueError,TypeError):
        try:return max(1,parsedate_to_datetime(value).timestamp()-stamp)
        except (ValueError,TypeError,OverflowError):return 60


def _cached(path, key, questions, model, stamp):
    """Exact, validated local decisions need no provider execution slot."""
    target=db_path(path).resolve()
    if not target.exists():return None
    try:
        from contextlib import closing
        with closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True,timeout=.01)) as db:
            row=db.execute("SELECT result FROM decisions WHERE key=? AND status='complete' AND created_at>?",
                           (key,stamp-7*86400)).fetchone()
        if row:return dict(status='complete',cached=True,**validate(json.loads(row[0]),questions,model))
    except sqlite3.Error:
        # Missing/locked storage is not permission to trust a stale decision.
        # Continue through normal locked admission and provider cooldown checks.
        return None


def _admit(lock, deadline):
    while True:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            remaining=deadline-time.monotonic() if deadline is not None else 0
            if remaining<=0:return False
            time.sleep(min(.01,remaining))


def _evaluate(path,purpose,state,questions,baseline=None,*,caller=None):
    settings=config()
    if not settings['enabled']:return dict(status='disabled',**settings)
    baseline={k:v for k,v in (baseline or {}).items() if k in ('run_id','path','reason','types','retained_ids','synthetic','expected')
              or k in questions and isinstance(v,str) and v in questions[k].get('criteria',{})}
    body=json.dumps(dict(model=settings['model'],state=state,questions=questions),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    if len(body)>64000 or not 1<=len(questions)<=24:return {'status':'input_limit'}
    key=hashlib.sha256(VERSION.encode()+body).hexdigest()
    started=time.monotonic();stamp=time.time()
    deadline=started+PLAN_ADMISSION_SECONDS if purpose in ('question_plan','question_draft') else None
    try:
        cached=_cached(path,key,questions,settings['model'],stamp)
        if cached:return cached
        with db_path(path).with_suffix('.lock').open('a') as lock:
            if not _admit(lock,deadline):return {'status':'busy'}
            with connect(path) as db:
                # A decision may have completed while we waited for ownership.
                row=db.execute('SELECT result FROM decisions WHERE key=? AND status=\'complete\' AND created_at>?',(key,stamp-7*86400)).fetchone()
                if row:return dict(status='complete',cached=True,**validate(json.loads(row[0]),questions,settings['model']))
                wait=db.execute('SELECT until,reason FROM provider_wait WHERE id=1').fetchone()
                if wait and wait['until']>stamp:return dict(status='cooldown',retry_at=wait['until'],reason=wait['reason'])
            status='complete';result={};delay=0
            try:result=validate((caller or transport)(body),questions,settings['model'])
            except urllib.error.HTTPError as error:
                status='rate_limit' if error.code==429 else 'authentication' if error.code in (401,403) else 'provider_error'
                delay=retry_after(error.headers.get('Retry-After'),stamp) if error.code in (429,503) else 300
            except (TimeoutError,urllib.error.URLError,OSError):status='unavailable';delay=60
            except (ValueError,KeyError,TypeError):status='invalid_response';delay=60
            elapsed=round((time.monotonic()-started)*1000,2)
            with connect(path) as db:
                if delay:db.execute('INSERT OR REPLACE INTO provider_wait VALUES(1,?,?)',(time.time()+delay,status))
                db.execute('INSERT OR REPLACE INTO decisions VALUES(?,?,?,?,?,?,?,?)',
                           (key,purpose,settings['model'],status,json.dumps(baseline or {},ensure_ascii=False),json.dumps(result,ensure_ascii=False),elapsed,stamp))
                db.execute('DELETE FROM decisions WHERE key NOT IN (SELECT key FROM decisions ORDER BY created_at DESC LIMIT 10000)')
            return dict(status=status,cached=False,elapsed_ms=elapsed,**result)
    except (OSError,sqlite3.Error):return {'status':'storage_unavailable'}


def evaluate(path,purpose,state,questions,baseline=None,*,caller=None,subject=None):
    from decision_journal import begin, finish, safely
    identity=safely(begin,path,purpose,state,questions,baseline,config(),subject) if config()['enabled'] else None
    try:
        result=_evaluate(path,purpose,state,questions,baseline,caller=caller)
    except (ValueError,KeyError,TypeError):
        result={'status':'invalid_input'}
    finally:
        safely(finish,path,identity,locals().get('result',{'status':'interrupted'}))
    if identity is not None:result['observation_id']=identity
    return result


def decide(path,purpose,state,questions,baseline=None,*,subject=None):
    """Synchronous plans share the exact cache, provider lock and cooldown.

    Question plans can read exact cached decisions during shadow work, or wait
    at most 250ms for the same cross-process provider lock. No provider retries.
    Interactive plans and drafts share bounded admission; other decisions keep immediate fallback.
    """
    if not path or not config()['enabled']:return {'status':'disabled'}
    if purpose in ('question_plan','question_draft'):
        return evaluate(path,purpose,state,questions,baseline,subject=subject)
    if not PENDING.acquire(blocking=False):
        skipped(path,purpose,'pending_limit',subject)
        return {'status':'busy'}
    try:return evaluate(path,purpose,state,questions,baseline,subject=subject)
    finally:PENDING.release()


def skipped(path,purpose,reason,subject=None):
    if not path or not config()['enabled']:return
    from decision_journal import begin, finish, safely
    identity=safely(begin,path,purpose,{}, {}, {},config(),subject)
    safely(finish,path,identity,{'status':'skipped','reason':reason})


def observe(path,purpose,state,questions,baseline=None,*,subject=None):
    """Bounded background shadow: no queue growth and no added model wait."""
    if not path or not config()['enabled']:return None
    if not PENDING.acquire(blocking=False):
        skipped(path,purpose,'pending_limit',subject)
        return None
    def work():
        try:return evaluate(path,purpose,state,questions,baseline,subject=subject)
        finally:PENDING.release()
    try:return POOL.submit(work)
    except RuntimeError:PENDING.release();return None


def status(path):
    result=config();result.update(counts={},last=None)
    if not db_path(path).exists():return result
    try:
        with sqlite3.connect(f'file:{db_path(path)}?mode=ro',uri=True,timeout=.1) as db:
            result['counts']=dict(db.execute('SELECT status,count(*) FROM decisions GROUP BY status'))
            row=db.execute('SELECT purpose,status,elapsed_ms,created_at FROM decisions ORDER BY created_at DESC LIMIT 1').fetchone()
            if row:result['last']=dict(zip(('purpose','status','elapsed_ms','created_at'),row))
            row=db.execute('SELECT until,reason FROM provider_wait WHERE id=1').fetchone()
            if row and row[0]>time.time():result['provider_wait']=dict(retry_at=row[0],reason=row[1])
    except sqlite3.Error:result['storage_status']='unavailable'
    return result
