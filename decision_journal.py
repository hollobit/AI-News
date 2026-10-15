"""Append attempts, including cache hits, without storing source text or credentials."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit


def path_for(path):
    return Path(str(path) + '.decisions.sqlite3')


def source_refs(evidence):
    refs = []
    for entry in evidence[:24]:
        url = str(entry.get('source_url') or entry.get('url') or '')
        try:
            parts = urlsplit(url)
            if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
                url = ''
        except ValueError:
            url = ''
        refs.append(dict(id=str(entry.get('id', ''))[:200], title=str(entry.get('title') or entry.get('id') or '근거')[:240],
                         url=url[:4000], kind=str(entry.get('origin') or entry.get('source_kind') or entry.get('kind') or 'source')[:80]))
    return refs


def context(kind='', identity='', title='', evidence=(), **extra):
    return dict(kind=kind, identity=str(identity or '')[:200], title=str(title)[:240],
                sources=source_refs(evidence), historical_comparison=bool(extra.get('historical_comparison')))


def begin(path, purpose, state, questions, baseline, settings, subject=None):
    if not path:
        return None
    subject = subject or {}
    # Rebuild the allowed metadata: callers cannot smuggle arbitrary payloads.
    safe = context(subject.get('kind', ''), subject.get('identity', ''), subject.get('title', ''),
                   subject.get('sources', []), historical_comparison=subject.get('historical_comparison'))
    safe['sources'] = source_refs(subject.get('sources', []))
    base = {key: baseline[key] for key in ('path', 'reason', 'types', 'retained_ids') if key in (baseline or {})}
    fingerprint = hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with closing(sqlite3.connect(path_for(path), timeout=.2)) as db, db:
        db.executescript('''CREATE TABLE IF NOT EXISTS decision_observations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,purpose TEXT NOT NULL,model TEXT NOT NULL,
            subject_kind TEXT NOT NULL,subject_id TEXT NOT NULL,context_json TEXT NOT NULL,
            questions_json TEXT NOT NULL,baseline_json TEXT NOT NULL,input_hash TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,status TEXT NOT NULL,started_at REAL NOT NULL,finished_at REAL,
            cached INTEGER NOT NULL DEFAULT 0,elapsed_ms REAL,result_json TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS decision_subject ON decision_observations(subject_kind,subject_id,id);''')
        return db.execute('''INSERT INTO decision_observations(purpose,model,subject_kind,subject_id,context_json,
            questions_json,baseline_json,input_hash,owner_pid,status,started_at) VALUES(?,?,?,?,?,?,?,?,?,'running',?)''',
            (purpose, settings.get('model') or '', safe['kind'], safe['identity'], json.dumps(safe, ensure_ascii=False),
             json.dumps(questions, ensure_ascii=False), json.dumps(base, ensure_ascii=False), fingerprint, os.getpid(), time.time())).lastrowid


def finish(path, identity, result):
    if identity is None:
        return
    safe = {k: result[k] for k in ('answers', 'usage', 'reason', 'retry_at') if k in result}
    with closing(sqlite3.connect(path_for(path), timeout=.2)) as db, db:
        db.execute('''UPDATE decision_observations SET status=?,finished_at=?,cached=?,elapsed_ms=?,result_json=? WHERE id=?''',
                   (result['status'], time.time(), bool(result.get('cached')), result.get('elapsed_ms'), json.dumps(safe, ensure_ascii=False), identity))
        if identity % 50 == 0:
            db.execute('DELETE FROM decision_observations WHERE id < ? AND finished_at IS NOT NULL', (identity-10000,))


def application(path, identity, *, applied, reason):
    """Record actual plan use separately from a model's suggested choice."""
    if identity is None:return
    with closing(sqlite3.connect(path_for(path), timeout=.2)) as db, db:
        row=db.execute('SELECT result_json FROM decision_observations WHERE id=? AND purpose=\'question_plan\'',(identity,)).fetchone()
        if not row:return
        body=json.loads(row[0])
        body['application']=dict(scope='question_plan',applied=bool(applied),reason=str(reason)[:80])
        db.execute('UPDATE decision_observations SET result_json=? WHERE id=?',(json.dumps(body,ensure_ascii=False),identity))


def safely(action, *args, **kwargs):
    # Optional telemetry must never fail primary analysis, including on a full disk.
    try:
        return action(*args, **kwargs)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None
