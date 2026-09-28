"""Application sectors, distinct from national-impact priorities and risk grades."""
import re
import unicodedata
from collections import Counter
from functools import lru_cache

SECTORS = [
    {'id':'ax','label':'AX · AI 전환','terms':['AX','AI 전환','인공지능 전환','업무 자동화','업무자동화','기업 AI 도입','AI 도입','AI transformation','enterprise AI','workflow automation','business process automation']},
    {'id':'medical','label':'의료','terms':['의료','임상','환자','신약','의학','질병 진단','의료 진단','medical','healthcare','health care','clinical','patients','drug discovery','biomedical','diagnostic imaging']},
    {'id':'security','label':'보안','terms':['보안','사이버','취약점','해킹','프롬프트 인젝션','데이터 유출','랜섬웨어','cybersecurity','cyber security','AI security','prompt injection','data breach','ransomware','malware','adversarial attack']},
    {'id':'safety','label':'안전','terms':['안전','AI 정렬','모델 정렬','레드팀','비상정지','비상 정지','킬 스위치','킬스위치','AI safety','alignment','guardrail','guardrails','red teaming','red-teaming','kill switch','emergency stop','safe deployment']},
    {'id':'technology','label':'기술','terms':['모델','알고리즘','학습','추론','반도체','컴퓨팅','로봇','멀티모달','model','models','algorithm','algorithms','training','inference','semiconductor','robotics','multimodal','transformer','transformers','GPU','MCP','RAG']},
]


@lru_cache(maxsize=256)
def _pattern(term):
    pattern=r'\s+'.join(re.escape(p) for p in term.casefold().split())
    if term.isascii():pattern=r'(?<![a-z0-9])'+pattern+r'(?![a-z0-9])'
    return re.compile(pattern)


def _match(text, term):
    # A literal first token is necessary even when phrase whitespace varies.
    # Avoid a full negative-lookbehind regex scan for absent English cues.
    if term.casefold().split()[0] not in text:
        return False
    return bool(_pattern(term).search(text))


def classify_sectors(item):
    parts=[str(item.get(k) or '') for k in ('title','text','excerpt','abstract')]
    source=item.get('source_context') or {}
    if source.get('status')=='fetched':parts.extend(str(source.get(k) or '') for k in ('title','text'))
    text=unicodedata.normalize('NFKC',re.sub(r'https?://\S+',' ','\n'.join(parts))).casefold()
    result=[]
    for sector in SECTORS:
        matches=[term for term in sector['terms'] if _match(text,term)]
        if sector['id']=='ax' and 'AX' in matches and not any(_match(text,t) for t in ('AI','인공지능','전환','자동화')):
            matches.remove('AX')
        if matches:result.append({'id':sector['id'],'label':sector['label'],'matched_terms':matches})
    return result


def annotate_sectors(items):
    return [dict(item,sectors=classify_sectors(item)) for item in items]


def filter_sector(items, sector=''):
    if sector and sector not in {s['id'] for s in SECTORS}:raise ValueError('알 수 없는 응용 분야입니다.')
    annotated=annotate_sectors(items)
    return [item for item in annotated if not sector or any(s['id']==sector for s in item['sectors'])]


def sector_counts(items):
    counts=Counter()
    for item in items:
        sectors=item['sectors'] if 'sectors' in item else classify_sectors(item)
        counts.update({sector['id'] for sector in sectors})
    return [dict(id=s['id'],label=s['label'],count=counts[s['id']]) for s in SECTORS]
