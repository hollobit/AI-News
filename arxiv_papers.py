"""arXiv paper metadata, version-aware archive discovery and corpus-only trends."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit, urlencode
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler, build_opener
import xml.etree.ElementTree as ET

from link_groups import extract_links

_ID = re.compile(r'(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]{1,30}/\d{7}))(?P<version>v[1-9]\d*)?', re.I)
_API_LOCK = threading.Lock()
_ATOM = '{http://www.w3.org/2005/Atom}'
_ARXIV = '{http://arxiv.org/schemas/atom}'
MAX_XML_BYTES = 2_000_000


def parse_arxiv_id(value):
    value = str(value or '').strip()
    original = value
    if '://' in value:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'}
                    or parsed.username or parsed.password or parsed.port not in {None, 80, 443}):
                return None
            match = re.fullmatch(r'/(?:abs|pdf|html)/(.+?)(?:\.pdf)?/?', parsed.path)
            if not match:
                return None
            value = match[1]
        except ValueError:
            return None
    match = _ID.fullmatch(value)
    if not match:
        return None
    base = match['base'].lower()
    digits = base.split('/')[-1].split('.')[0]
    if not 1 <= int(digits[2:4]) <= 12:
        return None
    return {'paper_id': base, 'version': int(match['version'][1:]) if match['version'] else None,
            'source_url': original if '://' in original else 'https://arxiv.org/abs/'+value}


def init_papers(db):
    db.execute('CREATE TABLE IF NOT EXISTS arxiv_api_cooldown(id INTEGER PRIMARY KEY CHECK(id=1),retry_at REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS arxiv_papers(paper_id TEXT PRIMARY KEY,metadata_json TEXT NOT NULL DEFAULT \'{}\',status TEXT NOT NULL DEFAULT \'pending\',error TEXT NOT NULL DEFAULT \'\',fetched_at TEXT NOT NULL DEFAULT \'\')')
    db.execute('''CREATE TABLE IF NOT EXISTS arxiv_paper_mentions(paper_id TEXT NOT NULL,source_url TEXT NOT NULL,
        version INTEGER,mention_day TEXT NOT NULL,title TEXT NOT NULL,record_id TEXT NOT NULL,
        PRIMARY KEY(paper_id,source_url,record_id))''')
    db.execute('CREATE TABLE IF NOT EXISTS arxiv_metadata_jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,ids_json TEXT NOT NULL,created_at TEXT NOT NULL,error TEXT NOT NULL DEFAULT \'\',owner_pid INTEGER)')
    if 'owner_pid' not in {row[1] for row in db.execute('PRAGMA table_info(arxiv_metadata_jobs)')}:
        db.execute('ALTER TABLE arxiv_metadata_jobs ADD COLUMN owner_pid INTEGER')


def _rows(db, table):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone():
        return []
    cursor = db.execute('SELECT * FROM '+table)
    keys = [column[0] for column in cursor.description]
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def discover_papers(db):
    """Read raw Telegram URLs before canonicalization removes version suffixes."""
    init_papers(db)
    pending = {}
    for table in ('news', 'articles', 'archived_urls'):
        for row in _rows(db, table):
            if row.get('active') == 0:
                continue
            urls = extract_links(row.get('text') or '')
            urls.extend(row.get(key) or '' for key in ('source_url', 'original_url'))
            record = str(row.get('chat_id', ''))+':'+str(row.get('message_id', row.get('id', '')))
            for url in set(urls):
                parsed = parse_arxiv_id(url)
                if not parsed:
                    continue
                key = (parsed['paper_id'], url, record)
                previous = pending.get(key)
                title = str(row.get('title') or '')[:300] or (previous[4] if previous else '')
                day = str(row.get('telegram_day') or row.get('day') or row.get('published_at') or '')[:10]
                pending[key] = (parsed['paper_id'], url, parsed['version'], day or (previous[3] if previous else ''), title, record)
    with db:
        db.executemany('INSERT OR IGNORE INTO arxiv_papers(paper_id) VALUES (?)', [(key[0],) for key in pending])
        db.executemany('''INSERT INTO arxiv_paper_mentions VALUES (?,?,?,?,?,?)
            ON CONFLICT DO UPDATE SET version=excluded.version,mention_day=excluded.mention_day,title=excluded.title
            WHERE arxiv_paper_mentions.version IS NOT excluded.version
               OR arxiv_paper_mentions.mention_day IS NOT excluded.mention_day OR arxiv_paper_mentions.title IS NOT excluded.title''', pending.values())
    return len({key[0] for key in pending})


def parse_atom(body, requested_ids):
    if not isinstance(body, bytes) or len(body) > MAX_XML_BYTES or b'\x00' in body or re.search(br'<!DOCTYPE|<!ENTITY', body, re.I):
        raise ValueError('arXiv XML 크기 또는 엔티티 선언 오류')
    root = ET.fromstring(body)
    if root.tag != _ATOM+'feed':
        raise ValueError('arXiv Atom 응답이 아닙니다.')
    results = {}
    for entry in root.findall(_ATOM+'entry'):
        identity = parse_arxiv_id(entry.findtext(_ATOM+'id', ''))
        if not identity or identity['paper_id'] not in requested_ids:
            continue
        def text(tag):
            return re.sub(r'\s+', ' ', entry.findtext(tag, '')).strip()
        title, abstract = text(_ATOM+'title'), text(_ATOM+'summary')
        published, updated = text(_ATOM+'published'), text(_ATOM+'updated')
        if not title or not abstract or len(abstract) > 24000:
            raise ValueError('arXiv 제목·초록이 비어 있거나 너무 큽니다.')
        for stamp in (published, updated):
            datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        primary = entry.find(_ARXIV+'primary_category')
        results[identity['paper_id']] = {'paper_id': identity['paper_id'], 'title': title, 'abstract': abstract,
            'authors': [re.sub(r'\s+', ' ', author.findtext(_ATOM+'name', '')).strip() for author in entry.findall(_ATOM+'author')][:150],
            'categories': sorted({item.get('term') for item in entry.findall(_ATOM+'category') if item.get('term')}),
            'primary_category': primary.get('term', '') if primary is not None else '',
            'published': published, 'updated': updated, 'doi': text(_ARXIV+'doi'), 'journal_ref': text(_ARXIV+'journal_ref'),
            'metadata_version': identity['version'], 'source_url': 'https://arxiv.org/abs/'+identity['paper_id'],
            'metadata_source': 'arxiv_atom_api', 'evidence_scope': 'official_metadata_and_abstract'}
    return results


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('arXiv API 리디렉션을 허용하지 않습니다.')


def fetch_metadata(ids):
    if not ids or len(ids) > 50 or any(not parse_arxiv_id(value) or parse_arxiv_id(value)['paper_id'] != value for value in ids):
        raise ValueError('arXiv ID 요청 오류')
    url = 'https://export.arxiv.org/api/query?' + urlencode({'id_list': ','.join(ids), 'max_results': len(ids)})
    request = Request(url, headers={'User-Agent': 'HaruNewsPaperIndex/1.0 (personal research metadata reader)', 'Accept': 'application/atom+xml'})
    with build_opener(_NoRedirect()).open(request, timeout=30) as response:
        body = response.read(MAX_XML_BYTES+1)
    return parse_atom(body, set(ids))


class PaperService:
    def __init__(self, path, fetcher=None, start_worker=True):
        self.path = str(path)
        self.fetcher = fetcher or fetch_metadata
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='arxiv-metadata') if start_worker else None
        self.lock = threading.Lock()
        self.active = None
        self.closed = False
        with self.db() as db:
            init_papers(db)
            for job, pid in db.execute("SELECT id,owner_pid FROM arxiv_metadata_jobs WHERE status IN ('queued','running')").fetchall():
                if not pid:
                    continue
                try:
                    if not pid:
                        raise ProcessLookupError
                    os.kill(int(pid), 0)
                except ProcessLookupError:
                    if start_worker:
                        db.execute("UPDATE arxiv_metadata_jobs SET status='interrupted',error='프로세스 재시작 후 재요청 필요' WHERE id=?", (job,))
                    else:
                        db.execute("UPDATE arxiv_metadata_jobs SET status='queued',owner_pid=NULL WHERE id=?", (job,))
                except PermissionError:
                    pass

    def db(self):
        return sqlite3.connect(self.path, timeout=30)

    def cooldown_until(self, db):
        row = db.execute('SELECT retry_at FROM arxiv_api_cooldown WHERE id=1').fetchone()
        return row[0] if row else 0

    def refresh(self, ids=None, limit=20):
        limit = max(1, min(50, int(limit)))
        if ids is not None and not isinstance(ids, list):
            raise ValueError('논문 ID는 목록으로 지정해야 합니다.')
        with self.lock:
            if self.closed or self.active:
                raise RuntimeError('arXiv 메타데이터 조회가 실행 중이거나 종료되었습니다.')
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM arxiv_metadata_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                    raise RuntimeError('arXiv 메타데이터 조회가 실행 중입니다.')
                retry_at = self.cooldown_until(db)
                if retry_at > time.time():
                    return {'id': '', 'status': 'rate_limited', 'paper_ids': [], 'count': 0,
                            'retry_at': datetime.fromtimestamp(retry_at, timezone.utc).isoformat()}
                discover_papers(db)
                known = {row[0]: (row[1], row[2]) for row in db.execute('SELECT paper_id,status,fetched_at FROM arxiv_papers')}
                if ids is not None:
                    requested = []
                    for value in ids:
                        parsed = parse_arxiv_id(value)
                        if not parsed or parsed['paper_id'] not in known:
                            raise ValueError('보관된 arXiv 논문 ID만 조회할 수 있습니다.')
                        requested.append(parsed['paper_id'])
                else:
                    requested = sorted(known, key=lambda value: (known[value][0] == 'fetched', known[value][1], value))
                selected = []
                for identity in dict.fromkeys(requested):
                    status, stamp = known[identity]
                    ttl = timedelta(days=1) if status == 'fetched' else timedelta(hours=1)
                    if stamp and datetime.fromisoformat(stamp) > datetime.now(timezone.utc)-ttl:
                        continue
                    selected.append(identity)
                    if len(selected) == limit:
                        break
                if not selected:
                    return {'id': '', 'status': 'cached', 'paper_ids': [], 'count': 0}
                job = uuid.uuid4().hex
                db.execute('INSERT INTO arxiv_metadata_jobs VALUES (?,?,?,?,?,?)', (job, 'queued', json.dumps(selected), datetime.now(timezone.utc).isoformat(), '', os.getpid() if self.pool else None))
            if self.pool:
                self.active = job
                self.pool.submit(self._run, job, selected)
            return {'id': job, 'status': 'queued', 'paper_ids': selected, 'count': len(selected)}

    def _run(self, job, ids):
        try:
            with self.db() as db:
                db.execute("UPDATE arxiv_metadata_jobs SET status='running' WHERE id=?", (job,))
            # One API connection, >=3 seconds between requests, shared across
            # instances and across processes using the same archive database.
            with _API_LOCK, open(self.path+'.arxiv-api.lock', 'a+') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                with self.db() as db:
                    if self.cooldown_until(db) > time.time():
                        db.execute("UPDATE arxiv_metadata_jobs SET status='deferred',error='arXiv 재시도 대기 중' WHERE id=?", (job,))
                        return
                handle.seek(0)
                try:
                    last = float(handle.read() or 0)
                except ValueError:
                    last = 0
                time.sleep(max(0, 3-(time.time()-last)))
                try:
                    results = self.fetcher(ids)
                except HTTPError as exc:
                    if exc.code in {429, 503}:
                        retry_at = time.time() + 3600
                        value = exc.headers.get('Retry-After', '') if exc.headers else ''
                        try:
                            retry_at = max(retry_at, time.time() + float(value))
                        except (ValueError, TypeError):
                            try:
                                retry_at = max(retry_at, parsedate_to_datetime(value).timestamp())
                            except (ValueError, TypeError, OverflowError):
                                pass
                        with self.db() as db:
                            db.execute('INSERT INTO arxiv_api_cooldown VALUES (1,?) ON CONFLICT(id) DO UPDATE SET retry_at=MAX(retry_at,excluded.retry_at)', (retry_at,))
                    raise
                finally:
                    handle.seek(0)
                    handle.truncate()
                    handle.write(str(time.time()))
                    handle.flush()
            stamp = datetime.now(timezone.utc).isoformat()
            missing = []
            with self.db() as db:
                for identity in ids:
                    metadata = results.get(identity)
                    if not metadata:
                        missing.append(identity)
                        db.execute("UPDATE arxiv_papers SET status='failed',error=?,fetched_at=? WHERE paper_id=? AND status!='fetched'", ('API 응답에 해당 논문이 없습니다.', stamp, identity))
                    else:
                        metadata = dict(metadata, retrieved_at=stamp)
                        db.execute("UPDATE arxiv_papers SET metadata_json=?,status='fetched',error='',fetched_at=? WHERE paper_id=?", (json.dumps(metadata, ensure_ascii=False), stamp, identity))
                db.execute('UPDATE arxiv_metadata_jobs SET status=?,error=? WHERE id=?', ('partial' if missing else 'complete', '누락: '+', '.join(missing) if missing else '', job))
        except Exception as exc:
            error, stamp = str(exc)[:500], datetime.now(timezone.utc).isoformat()
            with self.db() as db:
                db.executemany("UPDATE arxiv_papers SET status='failed',error=?,fetched_at=? WHERE paper_id=? AND status!='fetched'", [(error, stamp, identity) for identity in ids])
                db.execute("UPDATE arxiv_metadata_jobs SET status='failed',error=? WHERE id=?", (error, job))
        finally:
            with self.lock:
                self.active = None

    def status(self):
        with self.db() as db:
            counts = dict(db.execute('SELECT status,COUNT(*) FROM arxiv_papers GROUP BY status'))
            jobs = _rows(db, 'arxiv_metadata_jobs')[-12:]
            retry_at = self.cooldown_until(db)
        active = next((r['id'] for r in jobs if r['status'] in ('queued','running')), None)
        return {'active': active, 'pending': int(bool(active)), 'counts': counts, 'jobs': jobs,
                'retry_at': datetime.fromtimestamp(retry_at, timezone.utc).isoformat() if retry_at > time.time() else None}

    def close(self):
        with self.lock:
            self.closed = True
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)


def _paper_rows(db):
    records = _rows(db, 'arxiv_papers')
    mentions = defaultdict(list)
    for row in _rows(db, 'arxiv_paper_mentions'):
        mentions[row['paper_id']].append(row)
    analyses = {row['paper_id']: row for row in _rows(db, 'arxiv_paper_analyses')}
    items = []
    for row in records:
        saved = json.loads(row['metadata_json'])
        item = dict(saved, id=row['paper_id'], paper_id=row['paper_id'],
                    source_url='https://arxiv.org/abs/'+row['paper_id'], metadata_status=row['status'],
                    metadata_error=row['error'], fetched_at=row['fetched_at'])
        item.setdefault('title', next((m['title'] for m in mentions[row['paper_id']] if m['title']), 'arXiv '+row['paper_id']))
        for field in ('abstract', 'primary_category', 'published', 'updated', 'doi', 'journal_ref'):
            item.setdefault(field, '')
        for field in ('authors', 'categories'):
            item.setdefault(field, [])
        item.setdefault('metadata_version', None)
        item['title_source'] = (saved.get('metadata_source','arxiv_atom_api')+'_metadata' if saved.get('metadata_source') in {'openalex','semantic_scholar'} else 'arxiv_metadata') if saved.get('title') else 'telegram_mention'
        item['metadata_fetched_at'] = saved.get('retrieved_at', '')
        item['mentions'] = sorted(mentions[row['paper_id']], key=lambda m: m['mention_day'], reverse=True)
        item['mention_count'] = len({m['record_id'] for m in item['mentions']})
        item['observed_versions'] = sorted({m['version'] for m in item['mentions'] if m['version'] is not None})
        from sector_taxonomy import classify_sectors
        item['sectors'] = classify_sectors({'title': item['title'], 'text': item['abstract']})
        analysis = analyses.get(row['paper_id'])
        item['analysis_status'] = analysis['status'] if analysis else 'unassessed'
        if analysis:
            from paper_analysis import analysis_input_hash
            if analysis['input_hash'] != analysis_input_hash(item) and analysis['status'] not in {'queued', 'running'}:
                item['analysis_status'] = 'stale'
        items.append(item)
    return items


def paper(db, identity):
    parsed = parse_arxiv_id(identity)
    if not parsed:
        return None
    init_papers(db)
    return next((item for item in _paper_rows(db) if item['paper_id'] == parsed['paper_id']), None)


def _trends(db, items, window):
    from morphology import keyword_records
    from keyword_index import keyword_record_id
    dated = []
    for item in items:
        if item['metadata_status'] != 'fetched':
            continue
        try:
            dated.append((date.fromisoformat(item['published'][:10]), item))
        except ValueError:
            pass
    end = max((day for day, _ in dated), default=date.today())
    start, before = end-timedelta(days=window-1), end-timedelta(days=2*window-1)
    totals, categories, terms, labels = [set(), set()], defaultdict(lambda: [set(), set()]), defaultdict(lambda: [set(), set()]), {}
    selected = [(day, item) for day, item in dated if before <= day <= end]
    records = [dict(item, message_id=item['paper_id'], text=item['abstract'], day=day.isoformat()) for day, item in selected]
    morph = keyword_records(db, records) if records else {}
    for (day, item), record in zip(selected, records):
        win, identity = int(day >= start), item['paper_id']
        totals[win].add(identity)
        for category in item['categories']:
            categories[category][win].add(identity)
        for term in morph.get(keyword_record_id(record), []):
            if term['kind'] == 'noun':
                continue
            terms[term['id']][win].add(identity)
            labels[term['id']] = term['label']
    def stats(identity, pair):
        previous, current = map(len, pair)
        return {'id': identity, 'current': current, 'previous': previous,
                'growth_pct': round((current-previous)/previous*100, 1) if previous else None,
                'new': not previous and bool(current), 'low_sample': min(current, previous) < 3,
                'share_change_pp': round(100*(current/max(1,len(totals[1]))-previous/max(1,len(totals[0]))), 2)}
    category_rows = [stats(key, pair) for key, pair in categories.items()]
    keyword_rows = [dict(stats(key, pair), label=labels[key]) for key, pair in terms.items() if len(pair[1]) >= 2]
    keyword_rows.sort(key=lambda item: (-item['share_change_pp'], -item['current'], item['label']))
    return {'window_days': window, 'start': start.isoformat(), 'end': end.isoformat(), 'baseline_start': before.isoformat(),
            'current_documents': len(totals[1]), 'previous_documents': len(totals[0]), 'categories': category_rows,
            'keywords': keyword_rows[:16],
            'method': '조회된 보관 논문의 최초 published 날짜 기준 같은 길이의 두 기간 비교. Telegram 언급 날짜·수정일과 구분하며 버전은 1논문으로 집계합니다. 분모는 조회된 선택 코퍼스이며 전체 과학계의 성장률이 아닙니다. 형태소 후보는 서로 다른 논문 2개 이상에서 실제 관측된 용어입니다.'}


def read_papers(db, params=None):
    from sector_taxonomy import sector_counts
    params = params or {}
    def one(key, default=''):
        value = params.get(key, default)
        return str(value[0] if isinstance(value, list) and value else value or default)
    discover_papers(db)
    all_items = _paper_rows(db)
    counts = Counter(item['metadata_status'] for item in all_items)
    items = all_items
    query = one('q').casefold()
    if query:
        items = [item for item in items if query in (' '.join([item['paper_id'], item['title'], item['abstract'], *item['authors']])).casefold()]
    if one('category'):
        items = [item for item in items if one('category') in item['categories']]
    if one('sector'):
        items = [item for item in items if any(s['id'] == one('sector') for s in item['sectors'])]
    if one('analysis_status'):
        items = [item for item in items if item['analysis_status'] == one('analysis_status')]
    items.sort(key=lambda item: (item['published'], item['paper_id']), reverse=True)
    page, size = max(1,int(one('page','1'))), max(1,min(50,int(one('page_size','20'))))
    window = int(one('window_days', one('window','14')))
    if window not in {14,28}:
        raise ValueError('논문 비교 기간은 14일 또는 28일입니다.')
    return {'items': items[(page-1)*size:page*size], 'total': len(items), 'page': page, 'page_size': size,
            'coverage': {'discovered': len(all_items), 'fetched': counts['fetched'], 'failed': counts['failed'], 'pending': counts['pending']},
            'categories': [{'id': key, 'count': value} for key,value in sorted(Counter(c for item in all_items for c in item['categories']).items())],
            'sectors': sector_counts([dict(item, text=item['abstract']) for item in items]),
            'trends': _trends(db, items, window), 'limits': {'metadata_per_request': 50, 'api_min_interval_seconds': 3, 'full_text_read': False}}
