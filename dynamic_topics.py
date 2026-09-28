"""Corpus-derived topics and control-change observations, never causal forecasts."""
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import json
import re
import unicodedata

from keyword_index import document_id, keyword_record_id
from morphology import item_text
from strategic_value import evaluate_news

VERSION = 'dynamic-morphology-topics-v6-interest-domains'
_KINDS = {'technical_dictionary','noun_phrase','proper_noun','model_identifier'}
# These are contextual gates, not a topic vocabulary. Labels come only from
# observed linguistic records, allowing new technical/policy topics to emerge.
_AI = re.compile(r'(?i)(?<![a-z0-9])(?:ai|llm|agi|artificial intelligence)(?![a-z0-9])|인공지능|모델|에이전트|로봇')
_CONTROL = re.compile(r'(?i)탈옥|프롬프트.?인젝션|레드.?팀|워터마크|평가|한도|(?<![a-z])(?:jailbreak\w*|prompt.?injection|red.?team|watermark\w*|evaluation|quota\w*)(?![a-z])|통제|규제|감사|인증|허가|안전|감독|차단|검증|권한|보고 의무|평가 의무|정지|중단|회수|거버넌스|(?<![a-z])(?:control|regulat\w*|oversight|audit\w*|licens\w*|safety|governance|restrict\w*|suspend\w*|revoke\w*|shutdown|guardrail\w*|sandbox\w*)(?![a-z])')
_CHANGE = re.compile(r'(?i)탐지|차단|발견|성공|대응|(?<![a-z])(?:detect\w*|block\w*|discover\w*)(?![a-z])|도입|의무화|시행|강화|완화|금지|제한|중단|변경|철회|폐지|승인|허용|확대|축소|제안|채택|회수|무력화|우회|해제|(?<![a-z])(?:introduc\w*|mandat\w*|enact\w*|tighten\w*|relax\w*|ban\w*|restrict\w*|suspend\w*|revoke\w*|propos\w*|adopt\w*|lift\w*|bypass\w*|disable\w*|implement\w*)(?![a-z])')
_GOVERNANCE = re.compile(r'(?i)탈옥|프롬프트.?인젝션|레드.?팀|워터마크|(?<![a-z])(?:jailbreak\w*|prompt.?injection|red.?team|watermark\w*)(?![a-z])|규제|정책|정부|의회|의무|금지|안전|보안|권한|감독|감사|허가|승인|통제|인증|검증|거버넌스|비상.?정지|킬.?스위치|가드레일|(?<![a-z])(?:government|regulat\w*|polic(?:y|ies)|legislation|law|safety|security|permission\w*|oversight|audit\w*|licens\w*|governance|control\w*|guardrail\w*|kill.?switch)(?![a-z])')
_PROPOSED = re.compile(r'(?i)제안|권고|촉구|초안|(?<![a-z])(?:propos\w*|recommend\w*|draft|urge\w*)(?![a-z])')
_CONDITIONAL = re.compile(r'(?i)않|아니|없는|없다|만약|경우|가능성|검토|예정|계획|(?<![a-z])(?:not|never|without|if|would|could|might|may|conditional|consider\w*|plan\w*)(?![a-z])')
_EXCLUDED = {'학습률','learning rate','learning-rate'}
_GENERIC_ROLES = {'남자친구','여자친구','남편','아내','아버지','어머니','아빠','엄마','사용자','이용자','사람','인간','교수','학생','연구진','개발자','직원','고객','친구','boyfriend','girlfriend','husband','wife','user','users','customer','customers'}
_SIGNAL_MEANING = re.compile(r'(?i)탈옥|프롬프트.?인젝션|한도|(?<![a-z])(?:jailbreak\w*|prompt.?injection|quota\w*)(?![a-z])|통제|규제|검증|인증|감사|감독|권한|허가|승인|안전|보안|평가|워터마크|추적|격리|샌드박스|가드레일|킬.?스위치|정지|중단|회수|레드.?팀|접근.?심사|(?<![a-z])(?:control|regulat\w*|audit\w*|licens\w*|permission\w*|governance|safety|security|sandbox\w*|guardrail\w*|evaluation|monitoring|containment|interpretability|traceability|watermark\w*|attestation|shutdown|red.?team)(?![a-z])')


def _signal_label(term):
    label=unicodedata.normalize('NFKC',term.get('label','')).casefold()
    return (label not in _EXCLUDED | _GENERIC_ROLES and term.get('kind') in {'noun_phrase','technical_dictionary'}
            and bool(_SIGNAL_MEANING.search(label)))



def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def _alias(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value))).strip().casefold()



def _segments(item):
    """Exact fields and offsets used by morphology.item_text, without URL prose."""
    title=str(item.get('title') or '')
    body=str(item.get('text') or '')[:12000]
    segments=[(0,title,'telegram_title'),(len(title)+1,body,'telegram_excerpt')]
    source=item.get('source_context') or {}
    if source.get('status')=='fetched':
        offset=len(title)+1+len(body)+1
        fetched_title=str(source.get('title') or '')
        segments.extend([(offset,fetched_title,'fetched_url_title'),(offset+len(fetched_title)+1,str(source.get('text') or '')[:3500],'fetched_url_excerpt')])
    return segments


def _evidence(item,term,doc):
    start,end=term.get('start'),term.get('end')
    if not isinstance(start,int) or not isinstance(end,int) or start<0 or end<=start:
        return None
    surface=term.get('surface')
    for offset,text,origin in _segments(item):
        local=start-offset
        if local<0 or end-offset>len(text) or text[local:end-offset]!=surface:
            continue
        # URL mentions are not vocabulary evidence, even if a supplied record
        # claims their character offsets are a noun phrase.
        if any(match.start() < end-offset and match.end() > local for match in re.finditer(r'https?://\S+',text)):
            return None
        bounds=[0]
        for match in re.finditer(r'\n+|(?<=[.!?。！？])\s+',text):
            bounds.extend([match.start(),match.end()])
        bounds.append(len(text))
        left=max(pos for pos in bounds if pos<=local)
        right=min(pos for pos in bounds if pos>=end-offset)
        sentence=text[left:right]
        quote_start=left
        # A quote is bounded around the observed term; control gates below use
        # the full exact sentence, not unrelated paragraphs.
        if len(sentence)>700:
            quote_start=max(left,local-220)
            sentence=text[quote_start:min(right,quote_start+700)]
        full_sentence=text[left:right]
        cues = {'ai':list(dict.fromkeys(m[0] for m in _AI.finditer(full_sentence))),
                'control':list(dict.fromkeys(m[0] for m in _CONTROL.finditer(full_sentence))),
                'change':list(dict.fromkeys(m[0] for m in _CHANGE.finditer(full_sentence))),
                'governance':list(dict.fromkeys(m[0] for m in _GOVERNANCE.finditer(full_sentence)))}
        return {'document_id':doc,'url':item.get('source_url') or '',
                'title':str(item.get('title') or '')[:300],'quote':sentence,
                'origin':origin,'start':quote_start,'end':quote_start+len(sentence),
                'term_surface':surface,'day':item['day'],'matched_cues':cues,
                'control_change':all(cues.values()),
                'observation_type':'negated_or_conditional' if _CONDITIONAL.search(full_sentence) else 'proposed' if _PROPOSED.search(full_sentence) else 'reported_mention',
                'caution':'원문에 등장한 제안·조건·부정 또는 보도 표현이며, 정책의 실제 시행이나 통제 효과를 확인한 결과가 아닙니다.'}
    return None


def _strategic(item, term):
    priority=item.get('strategic_value') or evaluate_news(item)
    return bool(_AI.search(item_text(item)) and (priority['score']>0 or
        term.get('kind') in {'technical_dictionary','model_identifier'} or
        item.get('topic') in {'agents','models','robotics','hardware','policy','research'} or
        (_CONTROL.search(item_text(item)) and _CHANGE.search(item_text(item)))))


def _term_evidence(item,term,doc,control=False):
    candidates=[term]
    surface=term.get('surface') or ''
    if control and surface:
        for offset,text,_ in _segments(item):
            for match in re.finditer(re.escape(surface),text):
                candidates.append(dict(term,start=offset+match.start(),end=offset+match.end()))
    for candidate in candidates:
        evidence=_evidence(item,candidate,doc)
        if evidence and (not control or evidence['control_change']):return evidence
    return None


def _entry_terms(item,entry,keywords=None):
    metadata=entry.get('metadata') or {}
    kid=entry.get('keyword_id') or metadata.get('keyword_id')
    if entry.get('origin')!='manual' and kid and keywords is not None:
        return [term for term in keywords if term.get('id')==kid]
    found=[]
    for term in entry.get('terms') or [entry.get('label','')]:
        if not isinstance(term,str) or not term.strip():continue
        pattern=(r'(?<![a-zA-Z0-9])'+re.escape(term)+r'(?![a-zA-Z0-9])') if term.isascii() else re.escape(term)
        for offset,text,_ in _segments(item):
            for match in re.finditer(pattern,text,re.I):
                found.append({'id':kid or entry['id'],'label':entry.get('label',term),'kind':'manual_expression',
                              'surface':match[0],'start':offset+match.start(),'end':offset+match.end()})
    return found


def match_dynamic_item(item,entry,keywords=None):
    if entry.get('excluded'):return False
    if entry.get('id','').startswith('cooccurrence:'):
        from dynamic_cooccurrence import match_cooccurrence_item
        return match_cooccurrence_item(item,entry,keywords)
    signal=entry.get('kind') in {'signal','signals'} or entry.get('id','').startswith('dynamic-signal:')
    for term in _entry_terms(item,entry,keywords):
        if signal and unicodedata.normalize('NFKC',term['label']).casefold() in _EXCLUDED:continue
        if signal and entry.get('origin')!='manual' and not _signal_label(term):continue
        if entry.get('origin')!='manual' and not _strategic(item,term):continue
        if _term_evidence(item,term,document_id(item),control=signal):return True
    return False


def build_dynamic_topics(db,items,morph):
    """Register qualified source expressions; history never becomes new evidence."""
    from strategy_trends import LENSES
    builtin_aliases=defaultdict(set)
    for lens in LENSES:
        for label in [lens['name'],*lens['terms']]:
            builtin_aliases[_alias(label)].add(lens['id'])
    builtin_links=[]
    valid=[]
    for item in items:
        try: valid.append((date.fromisoformat(item.get('day','')),item))
        except (TypeError,ValueError): pass
    end=max((day for day,_ in valid),default=date.today())
    days=[(end-timedelta(days=13-i)).isoformat() for i in range(14)]
    current_start=end-timedelta(days=6); previous_start=end-timedelta(days=13)
    totals=[set(),set()]
    observed=defaultdict(dict); signals=defaultdict(dict); labels={}; priorities={}
    if db is not None:
        from dynamic_registry import list_registry
        registered=list_registry(db)['items']
    else:registered=[]
    manual=[entry for entry in registered if entry['origin']=='manual' and not entry['excluded'] and not entry['id'].startswith('cooccurrence:')]
    source_material=[]
    for day,item in valid:
        doc=document_id(item)
        window=1 if day>=current_start else 0 if day>=previous_start else -1
        if window>=0: totals[window].add(doc)
        priority=item.get('strategic_value') or evaluate_news(item)
        source_material.append((doc,day.isoformat(),_hash(item_text(item))))
        for term in morph.get(keyword_record_id(item),[]):
            if term.get('kind') not in _KINDS or not term.get('id') or not term.get('label'):
                continue
            if unicodedata.normalize('NFKC',term['label']).casefold() in _GENERIC_ROLES:continue
            if not _strategic(item,term):continue
            evidence=_term_evidence(item,term,doc)
            if not evidence: continue
            kid=term['id']; labels[kid]=term
            # A URL contributes once per day, never once per forwarded message.
            key=(doc,day.isoformat())
            observed[kid].setdefault(key,evidence)
            priorities[(kid,doc)]=max(priorities.get((kid,doc),1),1+.35*bool(priority['country_terms'])+.35*bool(priority['government_terms'])+priority['impact_score']/100)
            normalized=unicodedata.normalize('NFKC',term['label']).casefold()
            control_evidence=_term_evidence(item,term,doc,control=True) if _signal_label(term) else None
            if control_evidence:
                signals[kid].setdefault(key,control_evidence)
        for entry in manual:
            kid=entry['id']; signal=entry['kind']=='signal'
            if signal and unicodedata.normalize('NFKC',entry['label']).casefold() in _EXCLUDED:continue
            for term in _entry_terms(item,entry):
                evidence=_term_evidence(item,term,doc,control=signal)
                if evidence:
                    labels[kid]=term
                    (signals if signal else observed)[kid].setdefault((doc,day.isoformat()),evidence)
                    priorities[(kid,doc)]=1+.35*bool(priority['country_terms'])+.35*bool(priority['government_terms'])+priority['impact_score']/100
                    break
    source_hash=_hash((VERSION,sorted(source_material),sorted((kid,term['label'],term['kind']) for kid,term in labels.items())))
    previous={entry['id']:dict(entry.get('metadata') or {},label=entry['label'],version=entry['version'],
              keyword_id=(entry['id'] if entry['origin']=='manual' else (entry.get('metadata') or {}).get('keyword_id')),
              kind='signals' if entry['kind']=='signal' else 'topics')
              for entry in registered if entry['origin']!='builtin' and not entry['excluded'] and not entry['id'].startswith('cooccurrence:')}
    excluded={entry['id'] for entry in registered if entry['excluded']}
    manual_by_id={entry['id']:entry for entry in manual}
    output={'topics':[],'signals':[]}
    for kind,observations,minimum,prefix in [('topics',observed,3,'dynamic:'),('signals',signals,2,'dynamic-signal:')]:
        keys=set(observations)|{value['keyword_id'] for value in previous.values() if value.get('kind')==kind and value.get('keyword_id')}
        for kid in keys:
            identity=kid if kid in manual_by_id else prefix+kid
            if identity in excluded or (identity in manual_by_id and kid!=identity):continue
            prior=previous.get(identity)
            old_term=labels.get(kid) or {'label':(prior or {}).get('label',''),'kind':(prior or {}).get('term_kind','noun_phrase')}
            if kid not in manual_by_id and (unicodedata.normalize('NFKC',old_term['label']).casefold() in _GENERIC_ROLES or (kind=='signals' and not _signal_label(old_term))):continue
            evidence=list(observations.get(kid,{}).values())
            corpus_docs={entry['document_id'] for entry in evidence}
            active={entry['day'] for entry in evidence}
            qualifies=len(corpus_docs)>=minimum and (len(active)>=2 if kind=='topics' else True)
            if not qualifies and prior is None and kid not in manual_by_id: continue
            aliases=builtin_aliases.get(_alias(old_term['label']),set())
            if kid not in manual_by_id and aliases:
                builtin_links.append({'keyword_id':kid,'label':old_term['label'],'origin_kind':kind,
                                      'builtin_ids':sorted(aliases),'excluded_builtin_ids':sorted(aliases & excluded),
                                      'corpus_documents':len(corpus_docs),'match':'exact_normalized_alias',
                                      'basis':'기존 주제의 정확한 별칭이므로 자동 카드를 추가하지 않습니다. 통제 변화의 엄격한 문장 조건에 따른 관측 수를 기존 주제의 넓은 검색 통계에 합산하지 않습니다.'})
                continue
            pairs=[set(),set()]; timeline=defaultdict(set)
            for entry in evidence:
                day=date.fromisoformat(entry['day'])
                if previous_start<=day<=end:
                    pairs[int(day>=current_start)].add(entry['document_id'])
                    timeline[entry['day']].add(entry['document_id'])
            old,current=map(len,pairs)
            old_share=old/len(totals[0]) if totals[0] else 0
            share=current/len(totals[1]) if totals[1] else 0
            delta=round((share-old_share)*100,2)
            status=('needs_review' if not qualifies and current else 'dormant' if not current else
                    'emerging' if not old else 'growing' if delta>0 else 'cooling' if delta<0 else 'stable')
            label=labels.get(kid,{}).get('label') or prior['label']
            multiplier=sum(priorities.get((kid,doc),1) for doc in pairs[1])/current if current else 1
            evidence.sort(key=lambda entry:(entry['day'],entry['document_id']),reverse=True)
            distinct=[]; seen=set()
            for entry in evidence:
                if entry['document_id'] not in seen:
                    distinct.append(entry); seen.add(entry['document_id'])
            dto={'id':identity,'kind':kind,'keyword_id':kid,'label':label,'name':label,'terms':manual_by_id[kid]['terms'] if kid in manual_by_id else list(dict.fromkeys([label]+[entry['term_surface'] for entry in evidence]))[:12],
                 'subtitle':'실제 원문 표현에서 도출한 '+('AI 통제 변화 관측' if kind=='signals' else '전략 주제'),
                 'dynamic':True,'term_kind':labels.get(kid,{}).get('kind') or (prior or {}).get('term_kind'),'status':status,'current':current,'previous':old,'low_sample':min(current,old)<minimum,
                 'growth_pct':round((current-old)/old*100,1) if old else None,'share':round(share*100,2),'share_change_pp':delta,
                 'series':[len(timeline[day]) for day in days],'days':days,'corpus_documents':len(corpus_docs),'active_days':len(active),
                 'strategic_multiplier':round(multiplier,3),'strategic_score':round(delta*multiplier,3),
                 'evidence':[entry for entry in distinct if current_start.isoformat()<=entry['day']<=end.isoformat()][:3],
                 'historical_evidence':[entry for entry in distinct if entry['day']<current_start.isoformat()][:3],'verification':'rule_checked','verification_label':'원문 표현·문서 수 규칙 확인','source_hash':source_hash,
                 'count':current,'matched_documents':current,'zero_hits':current==0,'related_keywords':[],
                 'origin':'manual' if kid in manual_by_id else 'auto',
                 'basis':'형태소 표현의 원문 위치 및 고유 문서 수를 확인한 규칙 검사. LLM 검증·인과관계·통제 효과 확인이 아닙니다.',
                 'observation_types':{label:len({entry['document_id'] for entry in evidence if entry['observation_type']==label and current_start.isoformat()<=entry['day']<=end.isoformat()}) for label in ('proposed','negated_or_conditional','reported_mention')} if kind=='signals' else {},
                 'matched_cues':{name:sorted({term for entry in evidence for term in entry['matched_cues'][name]}) for name in ('ai','control','change','governance')} if kind=='signals' else {}}
            if kind=='signals':dto['_observations']=[{key:entry[key] for key in ('document_id','day','observation_type')} for entry in evidence if previous_start.isoformat()<=entry['day']<=end.isoformat()]
            output[kind].append(dto)
    # Deterministic ordering retains cooling and dormant topics in the catalog.
    for kind in output:
        output[kind].sort(key=lambda topic:(topic['status']=='dormant',-topic['strategic_score'],-topic['current'],topic['id']))
    if db is not None:
        from dynamic_registry import sync_automatic
        from strategy_trends import LENSES
        from strategy_monitoring import WATCH_LENSES
        watch={entry['id'] for entry in WATCH_LENSES}
        automatic=[]
        # Cap new registrations, never drop an already registered observation
        # merely because another topic now ranks higher.
        existing=[dto for values in output.values() for dto in values if dto['origin']!='manual' and dto['id'] in previous]
        new=[dto for values in output.values() for dto in values if dto['origin']!='manual' and dto['id'] not in previous]
        capacity=max(0,500-len(LENSES)-len(existing))
        new.sort(key=lambda dto:(-dto['strategic_score'],-dto['current'],dto['id']))
        ranked=sorted(existing+new,key=lambda dto:(dto['status']=='dormant',-dto['strategic_score'],-dto['current'],dto['id']))
        top_window=[dto for dto in ranked if dto['kind']=='topics'][:160]+[dto for dto in ranked if dto['kind']=='signals'][:160]
        new_window=[dto for dto in top_window if dto['id'] not in previous]
        new_window.sort(key=lambda dto:(-dto['strategic_score'],-dto['current'],dto['id']))
        chosen_new=new_window[:capacity]
        accepted={dto['id'] for dto in existing+chosen_new}
        omitted_new=len(new)-len(chosen_new)
        for kind in output:
            output[kind]=[dto for dto in output[kind] if dto['origin']=='manual' or dto['id'] in accepted]
            for dto in output[kind]:
                if dto['origin']=='manual':continue
                automatic.append({'id':dto['id'],'kind':'signal' if kind=='signals' else 'topic',
                                  'label':dto['label'],'terms':dto['terms'],'description':dto['subtitle'],
                                  'metadata':{key:value for key,value in dto.items() if key!='_observations'}})
        automatic.extend({'id':entry['id'],'kind':'signal' if entry['id'] in watch else 'topic',
                          'label':entry['name'],'terms':entry['terms'],'description':entry['subtitle'],
                          'origin':'builtin','metadata':{'builtin':True}} for entry in LENSES)
        saved=sync_automatic(db,automatic)
        registry={entry['id']:entry for entry in saved}
        for kind in output:
            output[kind]=[dict(dto,version=registry[dto['id']]['version']) for dto in output[kind]
                          if dto['id'] in registry and not registry[dto['id']]['excluded']]
    else:
        omitted_new=0
        for dto in output['topics']+output['signals']:dto['version']=1
    builtin_links.sort(key=lambda entry:(entry['origin_kind'],entry['keyword_id']))
    return dict(output,version=VERSION,builtin_alias_links=builtin_links,omitted_new_count=omitted_new,registration_limit=500,source_hash=source_hash,start=current_start.isoformat(),end=end.isoformat(),
                method='최신 보관 뉴스 날짜 기준 7일 대 이전 7일. 주제는 전체 보관 자료에서 고유 URL 3개·2일 이상, 통제 신호는 구체 통제·정책 의미의 원문 명사구이며 동일 문장 AI 대상+통제+변화+안전·정책·권한 문맥이 있는 고유 URL 2개 이상. 제안·부정·조건을 구분하고 실제 시행으로 단정하지 않음. 근거 없는 과거 주제는 비활성 상태로 보존. 동시 언급은 인과관계가 아닙니다.')
