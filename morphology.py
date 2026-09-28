"""Korean noun and technical phrase extraction with Kiwi and source spans.

This strategic index is separate from the lossless all-word search index.
No character fragments or arbitrary n-grams are used as strategic keywords.
"""
import hashlib
import json
import re
import threading
import unicodedata
from collections import OrderedDict
from concurrent.futures import Future
from strategy_monitoring import CONTROL_PHRASES

ENGINE_VERSION = 'kiwi-noun-phrases-v4-control-topics'
_LOCK = threading.Lock()
_KIWI = None
_RESULT_LOCK = threading.Lock()
_RESULTS = OrderedDict()
_INFLIGHT = {}
_RESULT_LIMIT = 8192


def _decoded_keywords(fingerprint, text, payload=None):
    with _RESULT_LOCK:
        if fingerprint in _RESULTS:
            _RESULTS.move_to_end(fingerprint)
            return _RESULTS[fingerprint]
        owner = fingerprint not in _INFLIGHT
        future = _INFLIGHT.setdefault(fingerprint, Future())
    if not owner:
        return future.result()
    try:
        value = json.loads(payload) if payload is not None else extract_keywords(text)
        with _RESULT_LOCK:
            _RESULTS[fingerprint] = value
            while len(_RESULTS) > _RESULT_LIMIT:
                _RESULTS.popitem(last=False)
        future.set_result(value)
        return value
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _RESULT_LOCK:
            _INFLIGHT.pop(fingerprint, None)
STOPWORDS = set('뉴스 소식 관련 기반 대한 통해 위한 이번 내용 소개 발표 공개 설명 분석 연구 기술 모델 기업 시장 개발 성능 결과 제공 가능 활용 주요 글로벌 최신 최초 최대 최초 변화 전략 확대 강화 구축 지원 출시 시작 진행 중심 때문 경우 부분 분야 수준 사용 적용 방법 문제 효과 결과 이후 기존 새로운 사실 확인 비교 평가 시사점 단독 직접 역대 자료 링크 제목 기사 기자 출처 오늘 내일 어제 금일 금년 다음 내용 주간 월간'.split())
STOPWORDS.update('핵심 논문 경쟁 세계 논의 지능 산업 완료 통합 설계 확산 재편 사업 투자 정부 국가 미국 중국 한국 유럽'.split())
STOPWORDS.update('규제 정책 행정부 의회 국회 장관 일본 영국 프랑스 독일 인도 캐나다 싱가포르 대한민국'.split())
ENGLISH_STOP = set('the a an this that these those in on of for with from to is are was were be been being it its as at by and or not no new news read more via source sources report reports article articles about into using use used model models ai research paper papers github arxiv infoq scmp reuters status category blog html com org net www'.split())
PHRASES = {
    '수출통제': ['수출통제', '수출 통제', 'export control', 'export controls'],
    'AI 안전성': ['AI 안전성', 'ai safety'],
    '모델 컨텍스트 프로토콜': ['model context protocol', 'MCP'],
    '그록킹': ['그록킹', 'grokking'],
    '확산 모델': ['확산 모델', 'diffusion model', 'diffusion models'],
    '희소 어텐션': ['희소 어텐션', 'sparse attention'],
    '컴퓨트 주권': ['컴퓨트 주권', 'compute sovereignty'],
    '신뢰 실행 환경': ['신뢰 실행 환경', 'trusted execution environment'],
    '소버린 AI': ['소버린 AI', '소버린AI', 'sovereign ai', 'AI 주권', '인공지능 주권'],
    'Physical AI': ['physical ai', '피지컬 AI', '피지컬AI'],
    'Agentic AI': ['agentic ai', '에이전틱 AI'],
    '강화학습': ['강화학습', '강화 학습', 'reinforcement learning'],
    '월드 모델': ['월드 모델', '월드모델', 'world model', 'world models'],
    '멀티모달': ['멀티모달', '멀티 모달', 'multimodal', 'multi-modal'],
    '온디바이스 AI': ['온디바이스 AI', 'on-device ai'],
    '추론 시점 연산': ['추론 시점 연산', 'test-time compute', 'test time compute'],
    '추론 스케일링': ['추론 스케일링', 'test-time scaling', 'inference-time scaling'],
    '전문가 혼합': ['전문가 혼합', '혼합 전문가', 'mixture of experts', 'mixture-of-experts', 'MoE'],
    '검색 증강 생성': ['검색 증강 생성', 'retrieval augmented generation', 'retrieval-augmented generation', 'RAG'],
    '비전·언어·행동 모델': ['vision-language-action', 'vision language action', 'VLA'],
    '모델 경량화': ['모델 경량화', 'model compression'],
    '지식 증류': ['지식 증류', 'knowledge distillation'],
    '연합학습': ['연합학습', '연합 학습', 'federated learning'],
    '합성 데이터': ['합성 데이터', 'synthetic data'],
    '데이터 주권': ['데이터 주권', 'data sovereignty'],
    '공급망': ['공급망', 'supply chain'],
    '양자화': ['양자화', 'quantization'],
    '휴머노이드': ['휴머노이드', 'humanoid', 'humanoids'],
    '에이전트': ['에이전트', 'agent', 'agents'],
    '오픈웨이트': ['오픈웨이트', '오픈 웨이트', 'open-weight', 'open weights'],
    '오픈소스': ['오픈소스', '오픈 소스', 'open source', 'open-source'],
}
PHRASES.update(CONTROL_PHRASES)
PHRASES['학습률'] = ['학습률', 'learning rate', 'learning-rate']
USER_WORDS = ['소버린', '피지컬', '에이전틱', '에이전트', '멀티모달', '온디바이스', '휴머노이드', '오픈웨이트', '딥시크', '엔비디아', '트랜스포머', '파인튜닝', '소프트웨어', '데이터센터', '컴퓨팅', '로보틱스', '강화학습', '프런티어', '벤치마크', '그록킹', '디퓨전']


def analyzer():
    """Load the actual morphology model; absence is an explicit installation error."""
    global _KIWI
    with _LOCK:
        if _KIWI is None:
            try:
                from kiwipiepy import Kiwi
            except ImportError:
                raise RuntimeError('전략 키워드에는 Kiwi 형태소 분석기가 필요합니다. .venv/bin/python으로 실행해 주세요.') from None
            _KIWI = Kiwi(num_workers=2)
            for word in USER_WORDS:
                _KIWI.add_user_word(word, 'NNP')
    return _KIWI


def normalized(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip().casefold()


def extract_keywords(text):
    """Extract nouns and contiguous noun phrases, retaining exact original spans."""
    # Keep source character offsets intact (NFKC and casefold may change length).
    # Preserve offsets when removing URLs. A URL path is not news vocabulary.
    text = re.sub(r'https?://\S+', lambda m: ' ' * len(m[0]), text)
    tokens = analyzer().tokenize(text)
    candidates = {}
    protected = []
    def add(label, start, end, kind, tags):
        if not label.strip() or len(label) > 60:
            return
        key = normalized(label)
        if key in ENGLISH_STOP or key in STOPWORDS:
            return
        if key not in candidates:
            candidates[key] = {'id': hashlib.sha256(('morph\0'+key).encode()).hexdigest()[:16],
                               'label': label, 'normalized': key, 'kind': kind,
                               'surface': text[start:end], 'start': start, 'end': end,
                               'pos': tags, 'count': 0}
        candidates[key]['count'] += 1
    for label, variants in PHRASES.items():
        matched_spans = set()
        for value in variants:
            pattern = re.escape(value.casefold())
            if value.isascii():
                pattern = r'(?<![a-z0-9])'+pattern+r'(?![a-z0-9])'
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matched_spans.add(match.span())
        accepted_spans = []
        for start, end in sorted(matched_spans, key=lambda span: (-(span[1]-span[0]), span[0])):
            if any(start < b and end > a for a, b in accepted_spans):
                continue
            accepted_spans.append((start, end))
        for start, end in sorted(accepted_spans):
            protected.append((start, end))
            add(label, start, end, 'technical_dictionary', ['TERM'])
    # ASCII boundaries allow Korean particles directly after complete versions.
    for match in re.finditer(r'(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)+(?![A-Za-z0-9_-]|\.[A-Za-z0-9])', text):
        if any(char.isdigit() for char in match[0]) and not any(match.start() < b and match.end() > a for a,b in protected):
            protected.append((match.start(), match.end()))
            add(match[0], match.start(), match.end(), 'model_identifier', ['SL', 'SN'])
    chunk = []
    def flush():
        if not chunk:
            return
        # A maximal phrase is accepted only if it is a short contiguous noun sequence.
        # Longer lists are not sliced into invented combinations.
        if 2 <= len(chunk) <= 4:
            start, end = chunk[0].start, chunk[-1].start + chunk[-1].len
            if all(not re.search(r'[^\s]', text[a.start+a.len:b.start]) for a,b in zip(chunk,chunk[1:])):
                surface = text[start:end]
                if len(surface) <= 40:
                    add(surface, start, end, 'noun_phrase', [t.tag for t in chunk])
        chunk.clear()
    for token in tokens:
        start, end = token.start, token.start+token.len
        if any(start < b and end > a for a,b in protected):
            flush(); continue
        form = token.form
        if token.tag == 'SL' and not (form.isupper() or (form[0].isupper() and any(c.isupper() for c in form[1:]))):
            flush(); continue
        if token.tag not in {'NNG', 'NNP', 'SL'} or len(form) < 2 or normalized(form) in STOPWORDS | ENGLISH_STOP:
            flush(); continue
        # Only complete noun forms present at their source offsets are accepted.
        if normalized(text[start:end]) != normalized(form):
            flush(); continue
        if chunk and (token.sent_position != chunk[-1].sent_position or re.search(r'[^\s]',text[chunk[-1].start+chunk[-1].len:start])):
            flush()
        add(form, start, end, 'proper_noun' if token.tag=='NNP' else 'noun', [token.tag])
        chunk.append(token)
    flush()
    return sorted(candidates.values(), key=lambda item: (item['kind'] not in {'technical_dictionary','noun_phrase'}, -item['count'],item['normalized']))


def item_text(item):
    """Use article text with separate bounded fetched content; no URL or generated prose."""
    text = str(item.get('title') or '') + '\n' + str(item.get('text') or '')[:12000]
    source = item.get('source_context') or {}
    if source.get('status') == 'fetched':
        text += '\n' + source.get('title','') + '\n' + source.get('text','')[:3500]
    return text


def keyword_records(db, items):
    """Cache linguistic results by content, not by article identity or current date."""
    from keyword_index import keyword_record_id
    db.execute('CREATE TABLE IF NOT EXISTS morphology_cache (content_hash TEXT PRIMARY KEY, keywords_json TEXT NOT NULL)')
    prepared = [(item, item_text(item)) for item in items]
    prepared = [(item, text, hashlib.sha256((ENGINE_VERSION+'\0'+text).encode()).hexdigest()) for item,text in prepared]
    fingerprints = list(dict.fromkeys(key for _,_,key in prepared))
    cached = {}
    for start in range(0, len(fingerprints), 400):
        keys = fingerprints[start:start+400]
        cached.update(db.execute('SELECT content_hash,keywords_json FROM morphology_cache WHERE content_hash IN ('+','.join('?' for _ in keys)+')', keys))
    # Preserve the prior transaction boundary: expensive Kiwi work must not
    # keep a caller's SQLite writer lock alive.
    db.commit()
    pending = []
    records = {}
    for item, text, fingerprint in prepared:
        value = _decoded_keywords(fingerprint, text, cached.get(fingerprint))
        if fingerprint not in cached:
            cached[fingerprint] = json.dumps(value,ensure_ascii=False)
            pending.append((fingerprint,cached[fingerprint]))
        # The schema has scalar values plus one POS list. Copy that list too;
        # callers cannot mutate shared cache entries, without generic deepcopy.
        records[keyword_record_id(item)] = [dict(term, pos=list(term['pos'])) for term in value]
    if pending:
        db.executemany('INSERT OR REPLACE INTO morphology_cache VALUES (?,?)', pending)
        db.commit()
    return records
