"""Compare saved risk excerpts with current local evidence, independently of audits.

A matching source is not a freshness/date claim or an external fact check. A
mismatch means the saved excerpt is not the current selected source context.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date
from link_groups import canonical_url
from projection_cache import cached_read, revision_token


def evidence_index_key(evidence):
    material=[evidence.get(key) for key in ('id','url','origin','text','title','message_id','channel')]
    return hashlib.sha256(json.dumps(material,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def _text_hash(text,title):
    return hashlib.sha256(json.dumps([text,title],ensure_ascii=False).encode()).hexdigest()


def _snapshot_text(item):
    # Must remain identical to WorkflowService.create_run's excerpt construction.
    return '\n'.join(str(item.get(key) or '') for key in ('title','summary','text','description')).strip()[:2000]


def _current_index(db):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    source={}
    if 'source_excerpts' in tables:
        for url,payload in db.execute('SELECT canonical_url,result_json FROM source_excerpts'):
            try: parsed=json.loads(payload)
            except (ValueError,TypeError):continue
            if isinstance(parsed,dict):
                source[canonical_url(url)]={'status':parsed.get('status'),'has_text':bool(str(parsed.get('text') or '').strip()),
                    'text_hash':_text_hash(str(parsed.get('text') or '')[:3500],str(parsed.get('title') or ''))}
    news=defaultdict(set); messages=defaultdict(set); dates=defaultdict(set); matched_dates=defaultdict(set)
    ready=False
    required={'news':{'chat_id','message_id','text','published_at'},
              'articles':{'chat_id','message_id','item_index','title','text','day'},
              'archived_urls':{'chat_id','message_id','original_url','active','entity_type'}}
    if all(table in tables and columns <= {row[1] for row in db.execute('PRAGMA table_info("'+table+'")')}
           for table,columns in required.items()):
        from improvement_selection import all_corpus_items
        for item in all_corpus_items(db):
            text=_snapshot_text(item)
            url=canonical_url(item.get('source_url') or '')
            fingerprint=_text_hash(text,str(item.get('title') or '')[:300])
            if url:news[url].add(fingerprint)
            messages[(str(item.get('channel') or ''),str(item.get('message_id') or item.get('id') or ''))].add(fingerprint)
            if item.get('date_basis')=='article':
                try:day=date.fromisoformat(item.get('day','')).isoformat()
                except (TypeError,ValueError):continue
                if url:dates[url].add(day)
                matched_dates[fingerprint].add(day)
        ready=True
    return {'sources':source,'news':dict(news),'messages':dict(messages),'news_ready':ready,'dates':dict(dates),'matched_dates':dict(matched_dates)}


def evidence_source_states(db,evidence):
    """Batch result keyed by evidence_index_key, avoiding reused URL-id collisions.

    matched_current/changed/unavailable/unverifiable are local evidence states.
    Consumers must keep unknown/stale dates separate and must not encode missing
    confirmation as a zero/low risk score.
    """
    token=revision_token(db,('source',))
    index=cached_read(db,'risk-source-index',token,lambda:_current_index(db),copy_result=False)
    result={}
    for item in evidence:
        key=evidence_index_key(item)
        if key in result:continue
        origin=item.get('origin'); url=canonical_url(item.get('url') or '')
        text=str(item.get('text') or ''); title=str(item.get('title') or '')
        source_dates=[]
        status,reason='unverifiable','원문 위치 또는 지원되는 원문 종류를 확인할 수 없습니다.'
        if not text.strip():
            reason='저장된 원문 발췌가 비어 있습니다.'
        elif origin=='fetched_url_excerpt' and url:
            current=index['sources'].get(url)
            if not current or current.get('status')!='fetched' or not current.get('has_text'):
                status,reason='unavailable','현재 캐시에 유효한 조회 원문이 없습니다. 실패·차단은 낮은 위험을 뜻하지 않습니다.'
            elif _text_hash(text,title)==current['text_hash']:
                status,reason='matched_current','저장된 조회 원문의 제목·발췌가 현재 로컬 캐시와 일치합니다.'
                source_dates=sorted(index['dates'].get(url,[]))
            else:
                status,reason='changed','저장된 조회 원문의 제목 또는 발췌가 현재 로컬 캐시와 일치하지 않습니다.'
        elif origin in ('external_source','external_watch') and url and db.execute("SELECT 1 FROM sqlite_master WHERE name='source_versions'").fetchone():
            current=db.execute("SELECT v.title,v.text,h.last_status FROM source_health h JOIN source_versions v ON v.url=h.url AND v.hash=h.hash WHERE h.url=?",(url,)).fetchone()
            if not current or current[2]!='fetched':
                status,reason='unavailable','현재 읽기에 성공한 외부 원문 버전을 찾을 수 없습니다.'
            else:
                expected=(str(current[0])+'\n\n'+str(current[1])[:2000]).strip()[:2000]
                if _text_hash(text,title)==_text_hash(expected,str(current[0])[:300]):
                    status,reason='matched_current','외부 원문 제목·본문에서 고정한 분석 문맥이 현재 보관 버전과 일치합니다.'
                    source_dates=sorted(index['dates'].get(url,[]))
                else:status,reason='changed','분석 후 외부 원문 제목 또는 본문이 변경되었습니다.'
        elif origin=='telegram_excerpt' and index['news_ready']:
            candidates=(index['news'].get(url,set()) if url else index['messages'].get((str(item.get('channel') or ''),str(item.get('message_id') or '')),set()))
            if not candidates:
                status,reason='unavailable','현재 선택된 뉴스 문맥에서 해당 원문 위치를 찾을 수 없습니다.'
            elif _text_hash(text,title) in candidates:
                status,reason='matched_current','저장된 뉴스 제목·발췌가 현재 URL별 뉴스 문맥과 일치합니다.'
                source_dates=sorted(index['matched_dates'].get(_text_hash(text,title),[]))
            else:
                status,reason='changed','저장된 뉴스 제목·발췌가 현재 URL별 대표 문맥과 다릅니다. 원문 편집 또는 대표 게시 변경을 재검토해야 합니다.'
        result[key]={'status':status,'reason':reason,'evidence_id':item.get('id',''),
                     'current_source_match':status=='matched_current','source_dates':source_dates,
                     'date_status':'dated_article_context' if source_dates else 'unknown',
                     'date_basis':'article_label' if source_dates else 'unknown',
                     'date_note':'현재 뉴스에 명시된 기사 날짜만 사용하며 Telegram 게시일·브리핑 날짜·조회 시각을 원기사 발행일로 대체하지 않습니다.',
                     'scope':'현재 로컬 원문과의 일치 확인이며 최신성·외부 사실 확인이 아닙니다.'}
    return result
