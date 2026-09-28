"""Persistent AI-control topics and observed, source-backed keyword associations."""
from collections import defaultdict
from functools import lru_cache
import re
import unicodedata

from keyword_index import document_id, keyword_record_id
from link_groups import canonical_url


WATCH_LENSES = [
    {'id': 'kill_switch', 'name': 'Kill switch · 비상정지', 'subtitle': '실행 중 시스템의 비상 정지',
     'description': '운영 중 시스템을 비상 정지하는 통제 수단. 학습 중단·배포 취소와 구분합니다.',
     'terms': ['kill switch', 'kill-switch', 'killswitch', '킬 스위치', '킬스위치', '비상정지', '비상 정지',
               'emergency shutdown', 'emergency stop', 'shutdown mechanism']},
    {'id': 'training_pause', 'name': '학습 일시중단', 'subtitle': '훈련 실행을 일시 멈추는 결정',
     'description': '학습 실행의 일시중단·유예. 시스템 비상정지나 이미 학습된 모델의 배포 중단과 다릅니다.',
     'terms': ['학습 일시중단', '학습 일시 중단', '학습 중단', '학습중단', '훈련 중단', '훈련 일시중단',
               'AI 개발 유예', 'training pause', 'pause training', 'pause ai training', 'pause training runs',
               'training moratorium', 'ai moratorium', 'halt training']},
    {'id': 'training_control', 'name': '학습 속도 조절', 'subtitle': '정책·안전을 위한 훈련 진행속도 조절',
     'description': '모델 개발·학습 진행의 속도 조절 단서. 최적화 하이퍼파라미터 learning rate(학습률)와 동의어가 아닙니다.',
     'terms': ['학습 속도 조절', '학습속도 조절', '학습 속도를 조절', '학습속도를 조절',
               '학습 속도 제한', '학습속도 제한', '훈련 속도 조절', '훈련 속도 제한',
               'training slowdown', 'slow down ai training', 'slowing ai training', 'training throttling']},
    {'id': 'compute_limit', 'name': '학습 연산량 제한', 'subtitle': '훈련 연산 예산·접근의 상한',
     'description': '학습 연산량·컴퓨트 사용량 제한. 평가·보고 의무의 연산량 기준 자체를 사용 금지나 상한으로 간주하지 않습니다.',
     'terms': ['학습 연산량 제한', '훈련 연산량 제한', '학습 연산량 상한', '연산량 상한',
               '컴퓨트 상한', '컴퓨트 제한', 'training compute cap', 'compute caps', 'compute cap',
               'training compute limit', 'training compute limits', 'flop cap', 'flop caps']},
    {'id': 'deployment_stop', 'name': '배포 중단', 'subtitle': '모델 배포·접근의 중단 및 회수',
     'description': '학습 완료 모델의 배포 중단·회수·접근 축소. 학습 실행의 중단과 별도로 관찰합니다.',
     'terms': ['배포 중단', '배포중단', '배포 일시중단', '모델 회수', '모델 접근 중단',
               'deployment pause', 'pause deployment', 'deployment halt', 'halt deployment',
               'deployment rollback', 'roll back deployment', 'model withdrawal', 'reduce access to deployed models']},
]
CONTROL_PHRASES = {topic['name'].split(' · ')[-1]: topic['terms'] for topic in WATCH_LENSES}
TERM_SOURCES = [
    {'title': 'UK Government: frontier AI development/training and deployment pauses',
     'url': 'https://www.gov.uk/government/publications/emerging-processes-for-frontier-ai-safety/emerging-processes-for-frontier-ai-safety'},
    {'title': 'NIST AI RMF: override, decommissioning and incident response',
     'url': 'https://airc.nist.gov/airmf-resources/playbook/manage/'},
    {'title': 'UK Government: compute thresholds identify regulatory scope; thresholds are not caps',
     'url': 'https://www.gov.uk/government/consultations/ai-regulation-a-pro-innovation-approach-policy-proposals/outcome/a-pro-innovation-approach-to-ai-regulation-government-response'},
    {'title': 'PyTorch Adam: lr is an optimizer learning-rate parameter',
     'url': 'https://docs.pytorch.org/docs/main/generated/torch.optim.adam.Adam_class.html'},
]


def source_text(item):
    parts = [str(item.get(key) or '') for key in ('title', 'text', 'excerpt')]
    source = item.get('source_context') or {}
    if source.get('status') == 'fetched':
        parts.extend(str(source.get(key) or '') for key in ('title', 'text'))
    return unicodedata.normalize('NFKC', re.sub(r'https?://\S+', ' ', '\n'.join(parts))).casefold()


@lru_cache(maxsize=16384)
def _ai_sentences(fields):
    ai_context=re.compile(r'(?i)(?<![a-z0-9])(?:ai|llm|agi|gpt|artificial intelligence|machine learning)(?![a-z0-9])|인공지능|언어.?모델|에이전트|로봇|신경망')
    result=[]
    for origin,body in fields:
        for sentence in re.split(r'\n+|(?<=[.!?。！？])\s+',body):
            text=unicodedata.normalize('NFKC',re.sub(r'https?://\S+',' ',sentence)).casefold()
            if ai_context.search(text):result.append((origin,sentence,text))
    return tuple(result)


@lru_cache(maxsize=128)
def _control_pattern(term):
    pattern=r'\s+'.join(re.escape(part) for part in term.casefold().split())
    if term.isascii():pattern=r'(?<![a-z0-9])'+pattern+r'(?![a-z0-9])'
    return re.compile(pattern)


@lru_cache(maxsize=128)
def _control_head(term):
    # The first token is required even when a multiword term allows variable
    # whitespace. Keep the regex for exact boundaries and whitespace semantics.
    return next(iter(term.casefold().split()), '')


def control_observations(item, topic):
    """Keep exact sentences with AI context; URL paths and unrelated paragraphs do not count."""
    from dynamic_topics import _AI, _CONDITIONAL, _PROPOSED
    fields=[(key,str(item.get(key) or '')) for key in ('title','text','excerpt')]
    source=item.get('source_context') or {}
    if source.get('status')=='fetched':fields.extend(('fetched_'+key,str(source.get(key) or '')) for key in ('title','text'))
    observations=[]
    for origin,sentence,text in _ai_sentences(tuple(fields)):
        matches=[term for term in topic['terms'] if _control_head(term) in text and _control_pattern(term).search(text)]
        if not matches:continue
        if topic['id']=='training_control' and re.search(r'learning[ -]rate|학습률|optimizer|최적화',text):
            if not re.search(r'정책|규제|안전|거버넌스|유예|moratorium|governance|policy|regulation|safety',text):continue
        observations.append({'quote':sentence,'origin':origin,'matched_terms':matches,
            'observation_type':'negated_or_conditional' if _CONDITIONAL.search(text) else 'proposed' if _PROPOSED.search(text) else 'reported_mention'})
    return observations


def match_topic(item, topic):
    return list(dict.fromkeys(term for row in control_observations(item,topic) for term in row['matched_terms']))


def build_monitoring(items, morph, lens_stats):
    """Use the caller's comparison-window corpus and already-cached morphology."""
    from datetime import date,timedelta
    current_start=(max((date.fromisoformat(i['day']) for i in items),default=date.today())-timedelta(days=6)).isoformat()
    stats = {lens['id']: lens for lens in lens_stats}
    topics = []
    excluded = set(CONTROL_PHRASES) | {'학습률'}
    for topic in WATCH_LENSES:
        documents, candidate_docs, term_info = {}, defaultdict(set), {}
        candidate_evidence = defaultdict(dict)
        observations = {}
        for item in items:
            observed = control_observations(item, topic)
            matches = list(dict.fromkeys(term for entry in observed for term in entry['matched_terms']))
            if not matches:
                continue
            doc = document_id(item)
            for observation in observed:
                observations[(doc,item['day'],observation['observation_type'])]={'document_id':doc,'day':item['day'],'observation_type':observation['observation_type']}
            url = canonical_url(item.get('source_url') or '')
            evidence = {'document_id': doc, 'title': str(item.get('title') or item.get('text') or '')[:180],
                        'source_url': url, 'url': url, 'day': item.get('day', ''), 'matched_terms': matches,**observed[0],
                        'caution':'AI 통제 주제의 문장 언급입니다. 실제 시행·효과를 확인한 결과가 아닙니다.'}
            if doc not in documents or evidence['day']>documents[doc]['day']:documents[doc]=evidence
            for term in morph.get(keyword_record_id(item), []):
                if term['kind'] == 'noun' or term['label'] in excluded:
                    continue
                candidate_docs[term['id']].add(doc)
                term_info[term['id']] = term
                candidate_evidence[term['id']].setdefault(doc, dict(evidence, keyword_surface=term['surface']))
        candidates = []
        for kid, docs in candidate_docs.items():
            if len(docs) < 2:
                continue
            term = term_info[kid]
            candidates.append({'id': kid, 'label': term['label'], 'kind': term['kind'],
                               'documents': len(docs), 'count': len(docs),
                               'topic_share_pct': round(100 * len(docs) / len(documents), 2),
                               'evidence': [candidate_evidence[kid][doc] for doc in sorted(docs)[:3]],
                               'basis': '같은 원문 문서에서 관측된 공동출현; 동의어·인과관계의 판정이 아닙니다.'})
        candidates.sort(key=lambda row: (-row['documents'], row['label']))
        values = stats.get(topic['id'], {})
        current_evidence=[e for e in documents.values() if e['day']>=current_start]
        topics.append(dict(topic, origin='builtin',_observations=list(observations.values()),label=topic['name'], matched_documents=len(documents), count=values.get('current',0),
                           observation_types={label:sum(e['observation_type']==label for e in current_evidence) for label in ('proposed','negated_or_conditional','reported_mention')},
                           current=values.get('current', 0), previous=values.get('previous', 0),
                           zero_hits=not current_evidence, evidence=sorted(current_evidence,key=lambda e:e['day'],reverse=True)[:5],
                           related_keywords=candidates[:8]))
    return {'topics': topics, 'sources': TERM_SOURCES,
            'method': '최신 뉴스 기준 현재 7일+직전 7일을 비교합니다. 신호 수와 인용은 현재 7일 기준이며 기본 통제 주제는 같은 문장의 AI 문맥을 확인합니다. 제안·조건·부정은 시행 사실과 구분합니다. 공동출현 후보는 14일 기준입니다. 정규 URL당 1문서로 중복 제거하고 Kiwi 명사구·고유명사·기술용어가 2개 이상 문서에서 함께 나타난 경우만 후보로 제시합니다. 후보 비율의 분모는 해당 주제의 14일 고유문서입니다. 공동출현은 인과·동의어가 아니며 0건은 선택 기간의 근거가 없다는 뜻입니다. 상시 관측 주제는 동향 갱신 때 재집계하며 외부 수집 작업을 자동 시작하지 않습니다.'}
