"""Strategic lenses and reproducible, corpus-relative trend measurements."""
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
import re
import unicodedata

from keyword_index import document_id, keyword_record_id
from strategic_value import DOMAINS, evaluate_news
from strategy_monitoring import WATCH_LENSES, build_monitoring, match_topic


LENSES = [
    {'id': 'china', 'name': '중국', 'subtitle': '모델 자립 · 산업 생태계',
     'terms': ['중국', 'china', 'chinese', 'deepseek', 'qwen', '알리바바', '화웨이', '바이두', '텐센트', '지푸', 'baidu', 'huawei']},
    {'id': 'us', 'name': '미국', 'subtitle': '프런티어 모델 · 플랫폼 경쟁',
     'terms': ['미국', 'united states', 'american', 'openai', 'anthropic', 'nvidia', '엔비디아', 'google', '구글', 'microsoft', '마이크로소프트', '백악관']},
    {'id': 'sovereign', 'name': '소버린 AI', 'subtitle': '주권 · 컴퓨팅 · 데이터',
     'terms': ['소버린', 'sovereign', '데이터 주권', 'ai 주권', '국가대표 ai', '자국', '국산', '독자 ai', '독자적 ai', 'sovereignty', 'localization', '자립']},
    {'id': 'physical', 'name': 'Physical AI', 'subtitle': '로보틱스 · 실세계 지능',
     'terms': ['physical ai', '피지컬', '로봇', 'robot', 'robotics', 'humanoid', '휴머노이드', 'embodied', '자율주행', 'world model', '월드 모델', 'vla']},
    {'id': 'agents', 'name': 'Agentic AI', 'subtitle': '자율 에이전트 · 업무 전환',
     'terms': ['agentic', 'agent', '에이전트', 'mcp', 'computer use', '컴퓨터 사용']},
    {'id': 'compute', 'name': 'AI 인프라', 'subtitle': '칩 · 데이터센터 · 에너지',
     'terms': ['gpu', 'hbm', 'tpu', '반도체', '데이터센터', 'data center', 'datacenter', '추론 비용', '전력', '컴퓨팅']},
    {'id': 'open', 'name': '오픈 모델', 'subtitle': '개방 생태계 · 배포 전략',
     'terms': ['open weight', 'open-weight', '오픈웨이트', '오픈 웨이트', '오픈소스', 'open source', 'open-source', 'llama', 'hugging face']},
]
# Interest domains remain explicit strategic lenses as well as sector filters.
from sector_taxonomy import SECTORS
LENSES.extend([
    {'id':'medical','name':'의료','subtitle':'임상 · 환자 · 신약 · 의료 AI 적용',
     'terms':list(next(s['terms'] for s in SECTORS if s['id']=='medical'))},
    {'id':'public_ax','name':'공공 AX','subtitle':'공공 서비스 · 행정 · 정부 AI 전환',
     'terms':['공공AX','공공 AX','공공AI','공공 AI','공공 인공지능','행정 AI','행정 인공지능',
              'AI 행정','AI 정부','인공지능 정부','정부 AI','디지털정부','디지털 정부',
              'public sector AI','government AI','AI government','AI in government']},
])
LENSES.extend(WATCH_LENSES)


@lru_cache(maxsize=1024)
def _lens_pattern(term):
    term=unicodedata.normalize('NFKC',term).casefold()
    return re.compile((r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])')
                      if term.isascii() else re.escape(term))


def matched_terms(item, terms):
    """Match text and successful retrieved excerpts, with ASCII word boundaries."""
    if not terms:
        return []
    source = item.get('source_context') or {}
    text = ' '.join(str(item.get(key) or '') for key in ('title', 'text', 'excerpt'))
    if source.get('status') == 'fetched':
        text += ' ' + source.get('title', '') + ' ' + source.get('text', '')
    text = unicodedata.normalize('NFKC', text).casefold()
    return [term for term in terms if unicodedata.normalize('NFKC',term).casefold() in text
            and _lens_pattern(term).search(text)]


def filter_lens(items, lens_id='', custom=''):
    """A company mention is a search cue, never proof of country attribution."""
    lens = next((item for item in LENSES if item['id'] == lens_id), None)
    if lens_id and not lens:
        raise ValueError('알 수 없는 전략 주제입니다.')
    terms = [term.strip().casefold() for term in custom.split(',') if term.strip()][:12]
    if not lens and not terms:
        return items
    def matches(item):
        base = match_topic(item, lens) if lens in WATCH_LENSES else matched_terms(item, (lens or {}).get('terms', []))
        return list(dict.fromkeys(base + matched_terms(item, terms)))
    selected = []
    for item in items:
        terms_found = matches(item)
        if terms_found:
            selected.append(dict(item, matched_terms=terms_found))
    return selected


def filter_strategic_keyword(db, items, params, records=None):
    """Resolve a morphological keyword by its stable identity, not substring search."""
    target = params.get('strategic_keyword', [''])[0]
    if not target:
        return items
    from morphology import keyword_records
    if records is None:
        records = keyword_records(db, items)
    return [item for item in items if any(term['id'] == target for term in records.get(keyword_record_id(item), []))]


def trend_metrics(db, items, records=None, precomputed=False):
    """Compare equal seven-day windows with URL deduplication and normalized shares."""
    shared_records = records
    valid = []
    for item in items:
        try:
            valid.append((date.fromisoformat(item['day']), item))
        except (ValueError, KeyError):
            continue
    end = max((day for day, _ in valid), default=date.today())
    start = end - timedelta(days=6)
    before = start - timedelta(days=7)
    days = [(before + timedelta(days=index)).isoformat() for index in range(14)]
    totals = [set(), set()]
    observations = defaultdict(lambda: [set(), set()])
    timeline = defaultdict(lambda: defaultdict(set))
    records = {}
    for day, item in valid:
        if not before <= day <= end:
            continue
        window = int(day >= start)
        doc = document_id(item)
        totals[window].add(doc)
        records[keyword_record_id(item)] = (window, doc, day.isoformat())
        for lens in LENSES:
            if (match_topic(item, lens) if lens in WATCH_LENSES else matched_terms(item, lens['terms'])):
                observations[lens['id']][window].add(doc)
                timeline[lens['id']][day.isoformat()].add(doc)
    def stats(pair):
        previous, current = map(len, pair)
        old_share = previous / len(totals[0]) if totals[0] else 0
        share = current / len(totals[1]) if totals[1] else 0
        return {'current': current, 'previous': previous,
                'growth_pct': round((current - previous) / previous * 100, 1) if previous else None,
                'share': round(share * 100, 2), 'share_change_pp': round((share - old_share) * 100, 2),
                'new': previous == 0 and current > 0, 'low_sample': min(current, previous) < 3}
    lenses = [dict(lens, **stats(observations[lens['id']]),
                   series=[len(timeline[lens['id']][day]) for day in days]) for lens in LENSES]
    from morphology import keyword_records
    morph = shared_records if shared_records is not None else keyword_records(db, items)
    terms = defaultdict(lambda: [set(), set()])
    labels, details = {}, {}
    term_days = defaultdict(lambda: defaultdict(set))
    country_docs, government_docs = defaultdict(set), defaultdict(set)
    impact_scores = defaultdict(dict)
    impact_domains = defaultdict(lambda: defaultdict(set))
    document_by_record = {keyword_record_id(item): item for item in items}
    for record_id, keywords in morph.items():
        if record_id not in records:
            continue
        window, doc, day = records[record_id]
        item = document_by_record[record_id]
        priority = item['strategic_value'] if precomputed and 'strategic_value' in item else evaluate_news(item)
        countries = priority['country_terms']
        governments = priority['government_terms']
        for term in keywords:
            kid = term['id']
            labels[kid] = term['label']
            details[kid] = term
            terms[kid][window].add(doc)
            term_days[kid][day].add(doc)
            if window:
                if countries: country_docs[kid].add(doc)
                if governments: government_docs[kid].add(doc)
                if priority['impact_score']:
                    # Reposts cannot multiply either document counts or priority.
                    impact_scores[kid][doc] = max(impact_scores[kid].get(doc, 0), priority['impact_score'])
                    for domain in priority['domains']:
                        impact_domains[kid][domain['id']].add(doc)
    emerging = []
    for kid, pair in terms.items():
        values = stats(pair)
        active_days = sum(bool(term_days[kid][day]) for day in days[7:])
        if values['current'] < 3 or active_days < 2 or values['share_change_pp'] <= 0:
            continue
        country_count, government_count = len(country_docs[kid]), len(government_docs[kid])
        mean_impact = sum(impact_scores[kid].values()) / values['current']
        multiplier = 1 + .35 * country_count / values['current'] + .35 * government_count / values['current'] + mean_impact / 100
        term = details[kid]
        if term['kind'] == 'noun':
            continue
        emerging.append(dict(id=kid, label=labels[kid], **values, active_days=active_days,
                             series=[len(term_days[kid][day]) for day in days],
                             kind=term['kind'], surface=term['surface'], pos=term['pos'],
                             country_documents=country_count, government_documents=government_count,
                             high_impact_documents=len(impact_scores[kid]),
                             impact_domains=[{'id': domain['id'], 'label': domain['label'], 'weight': domain['weight'],
                                              'documents': len(impact_domains[kid][domain['id']])}
                                             for domain in DOMAINS if impact_domains[kid][domain['id']]],
                             impact_mean_score=round(mean_impact, 3),
                             strategic_multiplier=round(multiplier, 3),
                             strategic_score=round(values['share_change_pp'] * multiplier, 3),
                             basis='Kiwi 형태소 분석: 명사·고유명사·연속 명사구 및 기술용어 사전'))
    emerging.sort(key=lambda item: (-item['strategic_score'], -item['current'], item['label']))
    return {'start': start.isoformat(), 'end': end.isoformat(), 'baseline_start': before.isoformat(),
            'weighting': '전략 점수 = 비중 변화(pp) × [1 + 0.35×국가 언급 문서 비율 + 0.35×정부·정책 언급 문서 비율 + 현재 기간 고유 문서의 평균 분야점수/100]. 분야점수 = min(60, 국가경제24+국가안보24+산업18+수출22+사회문제18+생활14+교육18 중 명시 단서가 있는 분야의 합). 분야가 없는 문서는 0점, 같은 URL 재게시의 분야점수는 최댓값 1개만 사용합니다. 가중치 최대 2.30배. 등장 건수·성장률·비중 변화는 가중 전 원래 값입니다. 문서 내 언급에 따른 검토 우선순위이며 실제 영향·사실성·정부의 공식 입장을 뜻하지 않습니다.', 'days': days, 'current_documents': len(totals[1]), 'previous_documents': len(totals[0]),
            'lenses': lenses, 'emerging': emerging[:18],
            'monitoring': build_monitoring([item for day, item in valid if before <= day <= end], morph, lenses),
            'method': '최신 뉴스 날짜 기준 7일 대 직전 7일. 각 기간에서 정규 URL당 1개 문서로 집계. 비중 변화는 기간별 전체 문서 수로 보정. 신규는 비교 기간에 없었다는 뜻이며 세계 최초가 아닙니다. 회사명은 탐색 단서이며 국가 귀속 판정이 아닙니다.'}
