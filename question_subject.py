"""Conservative literal scope for a question explicitly about one named item.

This is a search constraint, never an evidence/quality decision. Only a named
item followed by recognized research/evaluation facets is eligible. Unknown
requirements, broad topics and comparisons keep the ordinary retrieval path.
"""
import re
import unicodedata
from functools import lru_cache


PREFIX = re.compile(
    r'^(?:(?:최근|최신|요즘)\s+)?["“‘\']?'
    r'(?P<name>[A-Za-z][A-Za-z0-9._+-]*(?:[ \t]+[A-Za-z0-9][A-Za-z0-9._+-]*){0,7})'
    r'["”’\']?(?:의|에서|은|는|에\s*대한|에\s*대해)(?=\s)')
MULTI_SCOPE = re.compile(r'비교|차이|대비|각각|제외|말고|아닌|또는|\b(?:versus|vs|compare|except)\b', re.I)
FACET_WORDS = frozenset('''연구 현황 동향 평가 방법 방식 절차 실험 설계 벤치마크 데이터 데이터세트 데이터셋
    구성 종류 유형 규모 출처 수집 분포 분할 과제 범위 적용 성능 점수 수치 결과 해석 한계
    이슈 쟁점 조건 정확도 지표 재현성 일반화 상충 반대 근거 관계 설명 분석 확인한
    무엇인가요 무엇인가 무엇입니까 알려주세요 설명해주세요 설명해줘 주세요 및 와 과
    benchmark benchmarks evaluation methods dataset datasets data sets research survey
    task coverage scope protocol limitations issues risks challenges counterevidence
    provenance split metrics performance scores interpretation'''.split())


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split())


def broad_topic(name):
    from morphology import PHRASES
    from evidence_search import ALIASES
    return normalize(name).casefold() in {
        normalize(v).casefold() for label, aliases in {**PHRASES, **ALIASES}.items()
        for v in (label, *aliases)}


def subject(question):
    text = normalize(question)
    match = PREFIX.match(text)
    if not match or MULTI_SCOPE.search(text[match.end():]): return None
    name = match['name']
    if not 4 <= len(name) <= 100 or not any(len(word)>=3 for word in name.split()): return None
    from evidence_search import terms, SUFFIX
    remainder=[word for word in terms(text[match.end():]) if not word.startswith('concept:')]
    if any(SUFFIX.sub('',word).casefold() not in FACET_WORDS and word.casefold() not in FACET_WORDS
           for word in remainder):return None
    # A curated topic must retain all of its Korean/English aliases.
    if broad_topic(name) or name.split()[0].casefold() in {'what', 'how', 'tell', 'explain', 'compare'}:
        return None
    return name


@lru_cache(maxsize=128)
def pattern(name):
    return re.compile(r'(?<![A-Za-z0-9])' + re.escape(normalize(name)) + r'(?![A-Za-z0-9])', re.I)


def matches(name, *values):
    return any(pattern(name).search(normalize(value)) for value in values)


def expression(name):
    # Trigram FTS cannot search a two-character word. Literal matching after
    # the shortlist restores those words and requires the complete phrase.
    return '('+' AND '.join('"'+word.replace('"','""')+'"'
                            for word in name.split() if len(word)>=3)+')'
