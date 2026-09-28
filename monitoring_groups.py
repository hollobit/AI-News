"""Explainable semantic groups over observed signals, with exact document unions."""
from collections import defaultdict
from datetime import date,timedelta
import hashlib
import re
import unicodedata

# Grouping is presentation, not a new fact/causality or event-identity assertion.
FAMILIES = [
    ('deployment','AI 서비스·배포 중단',{'deployment_stop'},r'서비스.*중단|임시\s*중단|배포.*(?:중단|회수)|모델\s*회수|deployment (?:pause|halt|rollback)|model withdrawal'),
    ('safeguards','AI 안전장치·비상정지',{'kill_switch'},r'안전장치|비상\s*정지|킬\s*스위치|kill[ -]?switch|emergency (?:stop|shutdown)'),
    ('training-pause','AI 학습 일시중단',{'training_pause'},r'(?:학습|훈련).*(?:중단|유예)|training (?:pause|moratorium)|pause.*training'),
    ('training-speed','AI 학습 속도 조절',{'training_control'},r'(?:학습|훈련)\s*속도|training (?:slowdown|throttling)'),
    ('compute-cap','AI 학습 연산량 제한',{'compute_limit'},r'(?:연산량|컴퓨트).*(?:상한|제한)|compute (?:cap|limit)'),
    ('export-control','AI 수출통제',set(),r'수출\s*통제|export controls?'),
    ('cybersecurity','AI 사이버보안',set(),r'사이버\s*보안|국내\s*보안|cyber[ -]?security'),
    ('safety-governance','AI 안전성·거버넌스',set(),r'안전\s*거버넌스|AI\s*안전성|AI safety|safety governance'),
]


def family_for(signal):
    label=unicodedata.normalize('NFKC',signal.get('label') or signal.get('name') or '').strip()
    for key,name,ids,pattern in FAMILIES:
        if signal['id'] in ids or re.search(pattern,label,re.I):return 'signal-group:'+key,name
    normalized=re.sub(r'\s+',' ',label.casefold())
    return 'signal-group:observed-'+hashlib.sha256(normalized.encode()).hexdigest()[:16],label


def merge_signals(signals,start,end):
    """Use full observation identities, never sum totals or infer from sampled quotes."""
    previous_start=(date.fromisoformat(start)-timedelta(days=7)).isoformat()
    grouped=defaultdict(list)
    for signal in signals:
        grouped[family_for(signal)].append(signal)
    result=[]
    for (identity,label),members in grouped.items():
        periods=[set(),set()];kinds=defaultdict(set);timeline=defaultdict(set)
        evidence={}
        for member in members:
            for entry in member.get('_observations',[]):
                day=entry['day'];doc=entry['document_id']
                if not previous_start<=day<=end:continue
                current=day>=start
                periods[int(current)].add(doc);timeline[day].add(doc)
                if current:kinds[entry['observation_type']].add(doc)
            for e in member.get('evidence',[]):
                if start<=e.get('day','')<=end:
                    key=(e['document_id'],e.get('quote',''),e.get('origin',''))
                    row=evidence.setdefault(key,dict(e,signal_labels=[]))
                    member_label=member.get('label') or member['name']
                    if member_label not in row['signal_labels']:row['signal_labels'].append(member_label)
        if not periods[1] or not evidence:continue
        samples=sorted(evidence.values(),key=lambda e:(e['day'],e['document_id']),reverse=True)
        # Prefer one sample per document before additional phrases from the same document.
        unique=[];extra=[];seen=set()
        for e in samples:
            if e['document_id'] in seen:extra.append(e)
            else:unique.append(e);seen.add(e['document_id'])
        days=[(date.fromisoformat(previous_start)+timedelta(days=i)).isoformat() for i in range(14)]
        visible_members=[]
        for member in members:
            if not member.get('current',0):continue
            row={k:member.get(k) for k in ('id','label','name','current','previous','origin','dynamic','terms')}
            daily=defaultdict(set)
            for entry in member.get('_observations',[]):daily[entry['day']].add(entry['document_id'])
            row.update(days=days,series=[len(daily[day]) for day in days])
            visible_members.append(row)
        result.append({'id':identity,'name':label,'label':label,'grouped':True,'members':visible_members,
            'member_ids':[m['id'] for m in members],
            'terms':list(dict.fromkeys(t for m in members for t in m.get('terms',[]))),
            'current':len(periods[1]),'previous':len(periods[0]),'count':len(periods[1]),
            'series':[len(timeline[day]) for day in days],'days':days,
            'observation_types':{kind:len(docs) for kind,docs in kinds.items()},
            'evidence':(unique+extra)[:8],
            'basis':'유사한 통제 대상을 묶고 각 기간의 고유 문서 합집합으로 집계했습니다. 같은 사건·동일한 통제 수단이라는 판정은 아닙니다.',
            'verification_label':'고정·자동 관측 통합 · 인용 문장 규칙 확인',
            'grouping_basis':'통제 대상·행동의 명시적 표현 기준',
            'start':start,'end':end})
    return sorted(result,key=lambda g:(-g['current'],g['label']))


def filter_group(db,items,identity,records=None,registry=None):
    """Resolve current non-excluded constituent rules; preserve their exact matching gates."""
    from dynamic_registry import list_registry
    from strategy_monitoring import WATCH_LENSES,control_observations
    from dynamic_topics import match_dynamic_item
    from keyword_index import keyword_record_id
    from morphology import keyword_records
    if registry is None:registry=list_registry(db)['items']
    excluded={e['id'] for e in registry if e['excluded']}
    members=[dict(e,origin='builtin') for e in WATCH_LENSES if e['id'] not in excluded]
    members.extend(e for e in registry if e['kind']=='signal' and e['origin']!='builtin' and not e['excluded'])
    members=[e for e in members if family_for(e)[0]==identity]
    if not members:raise ValueError('존재하지 않거나 제외된 관측 신호 묶음입니다.')
    if records is None:records=keyword_records(db,items)
    result=[]
    for item in items:
        terms=[]
        for member in members:
            if member['origin']=='builtin':
                matches=control_observations(item,member)
                terms.extend(t for e in matches for t in e['matched_terms'])
            elif match_dynamic_item(item,member,records.get(keyword_record_id(item),[])):
                terms.extend(member['terms'])
        if terms:result.append(dict(item,matched_terms=list(dict.fromkeys(terms))))
    return result
