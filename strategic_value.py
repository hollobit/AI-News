"""Explainable editorial priority from explicit source-text impact cues.

Scores order news for review. They do not estimate truth, impact magnitude,
probability, country attribution, or market growth.
"""
import re
import unicodedata


COUNTRY_TERMS = ['중국', '미국', '한국', '대한민국', '일본', '영국', '프랑스', '독일', '인도',
                 '캐나다', '싱가포르', '사우디', '아랍에미리트', '유럽연합', 'china', 'united states',
                 'south korea', 'japan', 'india', 'france', 'germany', 'united kingdom']
GOVERNMENT_TERMS = ['정부', '행정부', '백악관', '국무원', '의회', '국회', '장관', '규제', '법안',
                    '산업정책', '공공조달', '수출통제', '과기정통부', '산업부', 'government',
                    'ministry', 'regulation', 'export control', 'export controls', 'white house']
DOMAINS = [
    {'id': 'economy', 'label': '국가경제', 'weight': 24, 'terms': [
        '국가경제', '국가 경제', '경제성장', '경제 성장', '거시경제', '국내총생산', '생산성',
        '고용', '실업', '일자리', '물가', '재정', '무역수지', '국가 경쟁력', '국가경쟁력',
        'national economy', 'economic growth', 'macroeconomics', 'gdp', 'productivity',
        'employment', 'unemployment', 'jobs', 'inflation', 'fiscal policy', 'trade balance']},
    {'id': 'security', 'label': '국가안보', 'weight': 24, 'terms': [
        '국가안보', '국가 안보', '국방', '군사', '방위산업', '방산', '사이버보안', '사이버 보안',
        '핵심 인프라', '핵심인프라', '수출통제', '수출 통제', '이중용도', '이중 용도',
        'national security', 'defense', 'defence', 'military', 'cybersecurity', 'cyber security',
        'critical infrastructure', 'export control', 'export controls', 'dual-use']},
    {'id': 'industry', 'label': '산업', 'weight': 18, 'terms': [
        '산업생산', '산업 생산', '산업정책', '산업 정책', '제조업', '제조 자동화', '제조자동화',
        '스마트공장', '스마트 공장', '공급망', '반도체', '산업 경쟁력', '산업경쟁력',
        'industrial production', 'industrial policy', 'manufacturing', 'factory automation',
        'smart factory', 'supply chain', 'semiconductor', 'semiconductors']},
    {'id': 'exports', 'label': '수출', 'weight': 22, 'terms': [
        '수출', '수출통제', '수출 통제', '해외시장 진출', '해외 시장 진출', '무역협정',
        '무역 협정', '관세', '통상', 'export', 'exports', 'export control', 'export controls',
        'trade agreement', 'tariff', 'tariffs', 'export competitiveness']},
    {'id': 'social', 'label': '사회적 문제', 'weight': 18, 'terms': [
        '사회적 문제', '사회문제', '불평등', '차별', '양극화', '취약계층', '취약 계층',
        '디지털 격차', '디지털격차', '실업', '일자리 대체', '일자리대체', '허위정보',
        '허위 정보', '개인정보', '프라이버시', '의료접근', '의료 접근', '사회복지',
        'inequality', 'discrimination', 'digital divide', 'unemployment', 'job displacement',
        'disinformation', 'privacy', 'healthcare access', 'social welfare']},
    {'id': 'life', 'label': '생활', 'weight': 14, 'terms': [
        '의료', '건강', '돌봄', '주거', '생활비', '생활 안전', '생활안전', '교통안전',
        '교통 안전', '재난', '공공서비스', '공공 서비스', '소비자 보호', '소비자보호',
        'healthcare', 'health care', 'public health', 'elder care', 'caregiving', 'housing',
        'cost of living', 'road safety', 'disaster', 'public services', 'consumer protection']},
    {'id': 'education', 'label': '교육', 'weight': 18, 'terms': [
        '교육', '학교', '교사', '학생', '직업훈련', '직업 훈련', '재교육', '평생학습',
        '평생 학습', '인재양성', '인재 양성', 'education', 'schools', 'school', 'teacher',
        'teachers', 'students', 'vocational training', 'reskilling', 'lifelong learning']},
]


def _pattern(term):
    pattern = r'\s+'.join(re.escape(part) for part in term.casefold().split())
    if term.isascii():
        pattern = r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])'
    return re.compile(pattern)


_PATTERNS = {term: _pattern(term) for term in COUNTRY_TERMS + GOVERNMENT_TERMS +
             [term for domain in DOMAINS for term in domain['terms']]}


def _source_text(item):
    parts = [str(item.get(key) or '') for key in ('title', 'text', 'excerpt')]
    source = item.get('source_context')
    if isinstance(source, dict) and source.get('status') == 'fetched':
        parts.extend(str(source.get(key) or '') for key in ('title', 'text'))
    text = re.sub(r'https?://\S+', ' ', '\n'.join(parts))
    return unicodedata.normalize('NFKC', text).casefold()


def evaluate_news(item):
    """Return bounded review priority and all matching rules for one news item.

    Each domain contributes once, even if several synonyms or repetitions match.
    Overlapping domains represent separate review concerns; their sum caps at 60.
    Country/company membership or a concrete impact is never inferred from cues.
    """
    text = _source_text(item)
    def matches(terms):
        return [term for term in terms if term.casefold().split()[0] in text and _PATTERNS[term].search(text)]
    countries, governments = matches(COUNTRY_TERMS), matches(GOVERNMENT_TERMS)
    domains = []
    for domain in DOMAINS:
        found = matches(domain['terms'])
        if found:
            domains.append({key: domain[key] for key in ('id', 'label', 'weight')} | {'matched_terms': found})
    impact = min(60, sum(domain['weight'] for domain in domains))
    score = min(100, (15 if countries else 0) + (25 if governments else 0) + impact)
    return {'score': score, 'level': 'high' if score >= 60 else 'medium' if score >= 30 else 'low',
            'domains': domains, 'country_terms': countries, 'government_terms': governments,
            'impact_score': impact, 'impact_factor': impact / 100,
            'explanation': '검토 우선순위 = 국가 언급 15 + 정부·정책 언급 25 + 분야별 가중치 합(최대 60), 총 0~100점. '
                           '분야별 가중치: 국가경제 24, 국가안보 24, 산업 18, 수출 22, 사회적 문제 18, 생활 14, 교육 18. '
                           '원문 안의 명시적 단서에 따른 분류이며 실제 영향·사실성·확률·국가 귀속의 판정이 아닙니다.'}
