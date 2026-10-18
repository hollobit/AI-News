"""Deterministic Korean/English lexical retrieval over existing, admitted evidence."""
from task_lifecycle import checkpoint
from collections import Counter, defaultdict, OrderedDict
from math import log1p
from functools import lru_cache
import re
import unicodedata
import sys
import threading
import marshal

VERSION = 'evidence-bm25-v1'
STOP = set('the and or of to in for with from is are what how about tell me please ai 인공지능 관련 대한 대해 어떤 무엇 무엇인가 어떻게 있는가 알려 주세요 알려주세요 설명 설명해 주세요 최근 최신 주요 동향 변화 비교 차이 관계 질문 근거 뉴스 기사 기술 전략 모델 사례 실행 조건 상충관계'.split())
SUFFIX = re.compile(r'(?:에서는|으로는|으로|에서|에게|까지|부터|보다|처럼|하고|이며|이라|라는|에는|은|는|이|가|을|를|의|와|과|도|에)$')
ALIASES = {
    '미국': ('미국', 'united states', 'u.s.', 'usa'),
    '중국': ('중국', 'china', 'chinese'),
    '한국': ('한국', '대한민국', 'south korea', 'korean'),
    '규제': ('규제', 'regulation', 'regulatory'),
    '정책': ('정책', 'policy', 'policies'),
    '반도체': ('반도체', 'semiconductor', 'semiconductors'),
    '비용': ('비용','cost','costs'),
    '도입': ('도입','adoption','adopt'),
    '데이터': ('데이터','data'),
    '주권': ('주권','sovereignty'),
}


def query_anchors(question):
    """Explicit technical concepts anchor a question; generic attributes do not."""
    from morphology import PHRASES
    anchors=set(terms(question)) & {'concept:'+label.casefold() for label in PHRASES}
    if len(anchors)>1 and not re.search(r'비교|차이|versus|\bvs\b',question,re.I):
        normalized=unicodedata.normalize('NFKC',question).casefold()
        matches=[(match.start(),-len(match[0]),'concept:'+canonical) for canonical,pattern in alias_patterns()
                 if 'concept:'+canonical in anchors for match in [pattern.search(normalized)] if match]
        if matches and min(matches)[0]<12:
            return {min(matches)[2]}
    return anchors


@lru_cache(maxsize=1)
def alias_rules():
    from morphology import PHRASES
    return [(canonical.casefold(), re.compile(r'(?<![a-z0-9])(?:'+
            '|'.join(re.escape(a.casefold()) for a in aliases)+r')(?![a-z0-9])'),
            tuple(a.casefold() for a in aliases))
            for canonical, aliases in {**PHRASES, **ALIASES}.items()]


@lru_cache(maxsize=1)
def alias_patterns():
    return [(canonical, pattern) for canonical, pattern, _ in alias_rules()]


def terms(value):
    """Normalize particles and curated technical aliases; no model or web request."""
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    tokens = []
    for word in re.findall(r'[a-z][a-z0-9_.+-]*|[가-힣]{2,}|\d{4}',value):
        checkpoint()
        if re.fullmatch('[가-힣]+',word):
            stem = SUFFIX.sub('',word)
            if len(stem)>=2:
                tokens.append(stem)
        tokens.append(word)
    for canonical, pattern, literals in alias_rules():
        # Every regex alternative is an escaped literal. Absence of all literals
        # proves a miss; actual matches still use the exact boundary expression.
        checkpoint()
        if any(literal in value for literal in literals) and pattern.search(value):
            tokens.append('concept:'+canonical)
    return [token for token in tokens if token not in STOP]


class _FieldCountsCache:
    """Pure text counts shared across query corpora, never source admission or ranks.

    Exact text keys and immutable payloads prevent stale counts or caller mutation.
    Retained Python payload estimates and entry count are both bounded. Oversized
    fields still participate in retrieval, without being retained in this cache.
    """
    def __init__(self, max_bytes=32*1024*1024, max_entries=8192, max_chars=32768):
        self.max_bytes, self.max_entries, self.max_chars = max_bytes, max_entries, max_chars
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, value):
        checkpoint()
        text = str(value or '')
        cacheable = len(text) <= self.max_chars
        if cacheable:
            with self.lock:
                entry = self.entries.get(text)
                if entry is not None:
                    self.entries.move_to_end(text)
            if entry is not None:
                return marshal.loads(entry[0])
        counts = tuple(Counter(terms(text)).items())
        checkpoint()
        if not cacheable:
            return counts
        # Avoid retaining thousands of separate Python objects for each field.
        # These bytes originate here and live only in this process: never load
        # marshal data from disk, a source document or an external request.
        payload = marshal.dumps(counts)
        # Include the text key, packed counts and an OrderedDict entry allowance.
        # This is a retained payload estimate, not a process RSS limit.
        size = sys.getsizeof(text) + sys.getsizeof(payload) + 256
        if size > self.max_bytes or self.max_entries <= 0:
            return counts
        with self.lock:
            # Tokenization is outside the lock, so unrelated requests can proceed.
            # Concurrent misses for identical text may compute the same result.
            previous = self.entries.pop(text, None)
            if previous is not None:
                self.bytes -= previous[1]
            self.entries[text] = (payload, size)
            self.bytes += size
            while self.bytes > self.max_bytes or len(self.entries) > self.max_entries:
                _, (_, removed_size) = self.entries.popitem(last=False)
                self.bytes -= removed_size
        return counts


_FIELD_COUNTS = _FieldCountsCache()


class LexicalIndex:
    """BM25 postings with field boosts; repeated queries never retokenize the corpus."""
    def __init__(self, rows):
        self.rows = rows
        self.postings = defaultdict(dict)
        self.lengths = {}
        for identity, fields in rows.items():
            checkpoint()
            counts = Counter()
            for text, boost in fields:
                checkpoint()
                for token, count in _FIELD_COUNTS.get(text):
                    checkpoint()
                    counts[token] += min(count, 4)*boost
            self.lengths[identity] = sum(counts.values())
            for token,count in counts.items():
                checkpoint()
                self.postings[token][identity] = count
        self.average = sum(self.lengths.values()) / max(1,len(rows)) or 1

    def search(self, question):
        scores = defaultdict(float)
        count = len(self.rows)
        for term in set(terms(question)):
            checkpoint()
            posting = self.postings.get(term,{})
            weight = log1p((count-len(posting)+.5)/(len(posting)+.5))
            for identity, frequency in posting.items():
                checkpoint()
                normalizer = 1.2*(.25+.75*self.lengths[identity]/self.average)
                scores[identity] += weight*frequency*2.2/(frequency+normalizer)
        return dict(scores)
