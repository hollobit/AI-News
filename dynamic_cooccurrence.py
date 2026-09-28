"""Observed strategic phrase pairs; same source occurrence never implies causality."""
from collections import defaultdict
from datetime import date,timedelta
from itertools import combinations
import hashlib
import json
import unicodedata
from dynamic_topics import _segments, _evidence, _AI, _KINDS
from dynamic_registry import sync_automatic,list_registry
from keyword_index import document_id,keyword_record_id


def _normal(text):return unicodedata.normalize('NFKC',text).casefold().strip()
def _hash(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def build_cooccurrence_candidates(db,items,morph):
    registered=[entry for entry in list_registry(db)['items'] if entry['id'].startswith('cooccurrence:')] if db is not None else []
    registered_ids={entry['id'] for entry in registered}
    identities={}
    dated=[]
    for item in items:
        try:dated.append((date.fromisoformat(item.get('day','')),item))
        except (TypeError,ValueError):pass
    end=max((day for day,_ in dated),default=date.today())
    days=[(end-timedelta(days=13-index)).isoformat() for index in range(14)]
    current_start=end-timedelta(days=6);previous_start=end-timedelta(days=13)
    totals=[set(),set()];pairs=defaultdict(dict);labels={};material=[]
    for day,item in dated:
        doc=document_id(item)
        if day>=previous_start:totals[int(day>=current_start)].add(doc)
        groups=defaultdict(dict)
        segments=_segments(item)
        for term in morph.get(keyword_record_id(item),[]):
            if term.get('kind') not in _KINDS or not term.get('id') or not term.get('label'):continue
            ev=_evidence(item,term,doc)
            if not ev:continue
            segment=next(((offset,text,origin) for offset,text,origin in segments if origin==ev['origin']),None)
            if not segment:continue
            offset,text,origin=segment
            priority=item.get('strategic_value') or {}
            if not (_AI.search(text) or priority.get('government_terms') or priority.get('country_terms') or priority.get('impact_score',0)>0):continue
            labels[term['id']]=term['label']
            groups[origin].setdefault(term['id'],(term,ev))
        bounded=sorted([(origin,term,ev) for origin,entries in groups.items() for term,ev in entries.values()],
                       key=lambda value:(value[1]['kind']!='technical_dictionary',-len(value[1]['label']),value[1]['id'],value[0]))[:10]
        groups=defaultdict(dict)
        for origin,term,ev in bounded:groups[origin][term['id']]=(term,ev)
        for origin,entries in groups.items():
            # Fixed per-source cap bounds pair expansion; dictionary concepts first.
            selected=sorted(entries.values(),key=lambda value:(value[0]['kind']!='technical_dictionary',-len(value[0]['label']),value[0]['id']))[:10]
            for (left,evleft),(right,evright) in combinations(selected,2):
                normleft,normright=_normal(left['label']),_normal(right['label'])
                if normleft in normright or normright in normleft:continue
                ids=tuple(sorted((left['id'],right['id'])))
                quote_records=[]
                for term,ev in ((left,evleft),(right,evright)):
                    offset=next(offset for offset,_,segment_origin in segments if segment_origin==origin)
                    quote_records.append({'keyword_id':term['id'],'label':term['label'],'surface':term['surface'],
                        'term_start':term['start']-offset,'term_end':term['end']-offset,
                        'quote':ev['quote'],'start':ev['start'],'end':ev['end']})
                evidence={'document_id':doc,'url':item.get('source_url') or '', 'day':day.isoformat(),
                    'title':str(item.get('title') or '')[:300],'origin':origin,'quotes':quote_records,
                    'quote':' / '.join(record['quote'] for record in quote_records),
                    'basis':'두 표현의 위치는 동일 출처 필드에서 확인했습니다. 결합된 새 용어나 인과관계라는 뜻은 아닙니다.'}
                pairs[ids].setdefault((doc,day.isoformat()),evidence)
                material.append((ids,doc,day.isoformat(),origin,_hash(quote_records)))
    # Existing definitions are re-observed independently of the discovery top-10 cap.
    # User edits change the matching phrases, never turn their old evidence into proof.
    for entry in registered:
        terms=entry['terms']
        existing_ids=tuple((entry.get('metadata') or {}).get('keyword_ids') or [])
        if entry['origin']!='manual' and existing_ids in pairs:
            identities[existing_ids]=entry['id']
            continue
        ids=tuple(entry['id']+':'+str(index) for index in range(len(terms)))
        identities[ids]=entry['id']
        labels.update(zip(ids,terms))
        pairs[ids]={}
        if len(terms)<2:continue
        for day,item in dated:
            doc=document_id(item);found=defaultdict(dict)
            for term in morph.get(keyword_record_id(item),[]):
                if term.get('kind') not in _KINDS:continue
                matches=[index for index,label in enumerate(terms) if _normal(label) in {_normal(str(term.get('label') or '')),_normal(str(term.get('surface') or ''))}]
                if not matches:continue
                evidence=_evidence(item,term,doc)
                if not evidence:continue
                offset=next(offset for offset,_,origin in _segments(item) if origin==evidence['origin'])
                for index in matches:
                    found[evidence['origin']][index]={'keyword_id':term.get('id'),'label':terms[index],'surface':term['surface'],
                        'term_start':term['start']-offset,'term_end':term['end']-offset,
                        'quote':evidence['quote'],'start':evidence['start'],'end':evidence['end']}
            for origin,quotes in found.items():
                if len(quotes)!=len(terms):continue
                records=[quotes[index] for index in range(len(terms))]
                value={'document_id':doc,'url':item.get('source_url') or '', 'day':day.isoformat(),
                    'title':str(item.get('title') or '')[:300],'origin':origin,'quotes':records,
                    'quote':' / '.join(record['quote'] for record in records),'basis':'등록된 표현을 동일 출처에서 다시 대조했습니다.'}
                pairs[ids].setdefault((doc,day.isoformat()),value)
                material.append((ids,doc,day.isoformat(),origin,_hash(records)))
    source_hash=_hash(sorted(material))
    output=[]
    for ids,observations in pairs.items():
        evidence=list(observations.values());documents={entry['document_id'] for entry in evidence}
        identity=identities.get(ids) or "cooccurrence:"+_hash(ids)[:24]
        if len(documents)<2 and identity not in registered_ids:continue
        if identity in registered_ids and ids not in identities:continue
        window=[set(),set()];timeline=defaultdict(set)
        for entry in evidence:
            day=date.fromisoformat(entry['day'])
            if day>=previous_start:
                window[int(day>=current_start)].add(entry['document_id']);timeline[entry['day']].add(entry['document_id'])
        previous,current=map(len,window)
        share=current/len(totals[1])*100 if totals[1] else 0
        previous_share=previous/len(totals[0])*100 if totals[0] else 0
        delta=round(share-previous_share,2)
        evidence.sort(key=lambda entry:(entry['day'],entry['document_id']),reverse=True)
        seen=set();examples=[]
        for entry in evidence:
            if entry['document_id'] not in seen:examples.append(entry);seen.add(entry['document_id'])
        label=' · '.join(labels[key] for key in ids)
        if len(label)>100:continue
        output.append({'id':identity,'kind':'topic','candidate_type':'cooccurrence',
            'origin':'auto','label':label,'name':label,'terms':[labels[key] for key in ids],
            'match_mode':'all','keyword_ids':list(ids),'dynamic':True,'documents':len(documents),'corpus_documents':len(documents),
            'current':current,'previous':previous,'growth_pct':round((current-previous)/previous*100,1) if previous else None,
            'share':round(share,2),'share_change_pp':delta,'series':[len(timeline[day]) for day in days],'days':days,
            'status':'needs_review' if current and len(documents)<2 else 'dormant' if not current else 'emerging' if not previous else 'growing' if delta>0 else 'stable',
            'evidence':examples[:3],'verification':'rule_checked','source_hash':source_hash,
            'description':'같은 기사 출처 안에서 함께 관측된 전략 표현 후보',
            'basis':'최신 보관 날짜 기준 7일 대 이전 7일. 고유 문서 2개 이상, 문서별 형태소 후보 최대 10개. 동시 등장은 인과·동의어·확산 증명이 아닙니다.'})
    output.sort(key=lambda value:(-value['current'],-value['documents'],value['id']))
    output=[dto for dto in output if dto['id'] in registered_ids]+[dto for dto in output[:32] if dto['id'] not in registered_ids][:max(0,500-len(registered_ids))]
    if db is None:return output
    live={dto['id']:dto for dto in output}
    records=sync_automatic(db,[dict(dto,metadata=dto) for dto in output],namespace='cooccurrence')
    result=[]
    for record in records:
        if not record['id'].startswith('cooccurrence:') or record['excluded'] or record['status']!='active':continue
        value=dict(live.get(record['id'],record['metadata']),match_mode='all',candidate_type='cooccurrence',id=record['id'],label=record['label'],name=record['label'],terms=record['terms'],
                   origin=record['origin'],version=record['version'],registry_status=record['status'])
        result.append(value)
    return result


def match_cooccurrence_item(item,entry,keywords):
    """AND matching requires every registered phrase in the same exact source field."""
    terms={_normal(term) for term in entry.get('terms',[]) if isinstance(term,str)}
    if len(terms)<2:return False
    found=defaultdict(set)
    for term in keywords:
        if term.get('kind') not in _KINDS:continue
        matches={_normal(str(term.get('label') or '')),_normal(str(term.get('surface') or ''))}&terms
        if not matches:continue
        evidence=_evidence(item,term,document_id(item))
        if evidence:found[evidence['origin']].update(matches)
    return any(terms<=observed for observed in found.values())
