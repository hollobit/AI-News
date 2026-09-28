"""Reusable, bounded public-page excerpts with explicit retrieval provenance."""
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import sqlite3
import threading
import re

from link_groups import canonical_url
from agent_reach_runtime import fetch_source

_FETCH_LOCK = threading.Lock()
_FETCHES = {}


def _search_evidence(result):
    if result.get('status') != 'fetched':
        return ('', '')
    return (str(result.get('title') or ''), str(result.get('text') or ''))


def source_quality_error(result):
    """Reject explicit empty/challenge shells, never short articles by length."""
    if result.get('status') != 'fetched':
        return ''
    text = str(result.get('text') or '').strip()
    if not text:
        return '유효한 기사 본문을 추출하지 못했습니다 (빈 본문).'
    normalized = re.sub(r'\s+', ' ', text).casefold()
    if normalized == 'go to end' and str(result.get('title') or '').strip().casefold() == 'your privacy choices':
        return '기사 본문 대신 개인정보 선택 안내 페이지가 반환되었습니다.'
    shells = (
        'cookies must be enabled',
        'enable cookies for pubmed.ncbi.nlm.nih.gov and reload this page to continue',
        'comprehensive up-to-date news coverage, aggregated from sources all over the world by google news',
        '正在进行安全检测',
    )
    # Restrict the diagnostic to small shells; an article quoting a challenge
    # message within substantive prose must not be rejected.
    if len(text) < 600 and any(shell in normalized for shell in shells):
        return '기사 본문 대신 접근 확인 또는 서비스 안내 페이지가 반환되었습니다.'
    return ''


def init_sources(db):
    """Create the shared URL cache; original messages are never modified."""
    db.execute("""CREATE TABLE IF NOT EXISTS source_excerpts (
        canonical_url TEXT PRIMARY KEY, result_json TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")


def source_map(db):
    """Read locally stored excerpts without causing network requests."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_excerpts'").fetchone():
        return {}
    return {row[0]: json.loads(row[1]) for row in db.execute(
        "SELECT canonical_url,result_json FROM source_excerpts")}


def attach_sources(db, items):
    """Attach only an article's primary URL to avoid mixing sibling news."""
    saved = source_map(db)
    return [dict(item, source_context=saved.get(canonical_url(item.get('source_url') or ''), {}))
            for item in items]


class SourceService:
    """Fetch at most four pages concurrently and cache bounded excerpts for seven days."""
    def __init__(self, path, fetcher=None):
        self.path = str(path)
        self.fetcher = fetcher or fetch_source
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='news-source')
        self.lock = threading.Lock()
        self.jobs = {}
        self.closed = False
        with sqlite3.connect(self.path, timeout=30) as db:
            init_sources(db)
            from source_store import init
            init(db)

    def fetch(self, url, refresh=False):
        fetch_url = str(url or '').strip()
        url = canonical_url(url)
        key = (str(Path(self.path).resolve()), url)
        with _FETCH_LOCK:
            owner = key not in _FETCHES
            future = _FETCHES.setdefault(key, Future())
        if not owner:
            return deepcopy(future.result())
        try:
            value = self._fetch(url, refresh, fetch_url=fetch_url)
            future.set_result(value)
            return deepcopy(value)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with _FETCH_LOCK:
                _FETCHES.pop(key, None)

    def _fetch(self, url, refresh=False, fetch_url=None):
        """Store retrieval independently of downstream model success."""
        url = canonical_url(url)
        if not url:
            raise ValueError('유효한 HTTP(S) URL이 필요합니다.')
        fetch_url = url if fetch_url is None else fetch_url
        if canonical_url(fetch_url) != url:
            raise ValueError('조회 URL이 원문 캐시 식별자와 일치하지 않습니다.')
        with sqlite3.connect(self.path, timeout=30) as db:
            row = db.execute('SELECT result_json,updated_at FROM source_excerpts WHERE canonical_url=?', (url,)).fetchone()
        if row and not refresh:
            cached = json.loads(row[0])
            age = timedelta(days=7) if cached['status'] == 'fetched' else timedelta(hours=1)
            if datetime.fromisoformat(row[1]) > datetime.now(timezone.utc) - age:
                return cached
        try:
            # URL normalization identifies a document; it must not add a
            # redirect by changing the publisher's actual submitted hostname.
            # fetch_source still validates every target and redirect normally.
            raw = self.fetcher(fetch_url)
        except Exception:
            raw = {'status': 'failed', 'error': '원문 조회에 실패했습니다.'}
        quality_error = source_quality_error(raw)
        if quality_error:
            raw = dict(raw, status='failed', error=quality_error, text='')
        text = str(raw.get('text') or '') if raw.get('status') == 'fetched' else ''
        stamp = datetime.now(timezone.utc).isoformat()
        result = {key: raw.get(key, '') for key in ('status', 'final_url', 'title', 'error', 'content_type','reader','platform','evidence_scope','published_at','media_count')}
        result.update(url=url, requested_url=fetch_url, fetched_at=stamp, text=text[:3500],
                      truncated=bool(raw.get('truncated')) or len(text) > 3500,
                      content_hash=hashlib.sha256(text[:3500].encode()).hexdigest(),
                      evidence_id='url_' + hashlib.sha256(url.encode()).hexdigest()[:24])
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT result_json FROM source_excerpts WHERE canonical_url=?', (url,)).fetchone()
            from source_store import save
            metadata=save(db,url,raw,stamp)
            result.update(metadata)
            changed = _search_evidence(json.loads(previous[0]) if previous else {}) != _search_evidence(result)
            db.execute('INSERT OR REPLACE INTO source_excerpts VALUES (?,?,?)',
                       (url, json.dumps(result, ensure_ascii=False), stamp))
            if metadata['content_changed'] and not changed and db.execute("SELECT 1 FROM sqlite_master WHERE name='projection_revisions'").fetchone():
                db.execute("UPDATE projection_revisions SET value=value+1 WHERE kind='source'")
            # Invalidate the existing incremental all-word index atomically.
            if changed and db.execute("SELECT 1 FROM sqlite_master WHERE name='state'").fetchone():
                from keyword_index import mark_keyword_source_changed
                mark_keyword_source_changed(db)
        return result

    def submit(self, urls):
        """Queue a visible bounded selection; arbitrary user URLs are not accepted by the API."""
        targets = {}
        for value in urls:
            identity = canonical_url(value)
            if identity:
                targets.setdefault(identity, str(value).strip())
        if len(targets) > 100:
            raise ValueError('한 번에 최대 100개 URL을 조회할 수 있습니다.')
        with self.lock:
            if self.closed or len(self.jobs) + len(targets) > 100:
                raise RuntimeError('원문 조회 대기열이 가득 찼거나 종료되었습니다.')
            for url, fetch_url in targets.items():
                if url not in self.jobs:
                    self.jobs[url] = self.pool.submit(self.fetch, fetch_url)
        return {'queued': len(targets)}

    def status(self):
        with self.lock:
            for url, job in list(self.jobs.items()):
                if job.done():
                    del self.jobs[url]
            pending = len(self.jobs)
        with sqlite3.connect(self.path, timeout=30) as db:
            counts = dict(db.execute("SELECT json_extract(result_json,'$.status'),COUNT(*) FROM source_excerpts GROUP BY json_extract(result_json,'$.status')"))
        return {'pending': pending, 'counts': counts, 'total': sum(counts.values())}

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
