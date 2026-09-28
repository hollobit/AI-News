"""Paginated risk review priorities: ordinal policy, never estimated probability."""
from collections import Counter
from datetime import date,datetime,timezone
import math
from projection_cache import cached_read,revision_token
from risk_analysis import read_risks,_digest,RISK_SCHEMA
from strategic_value import DOMAINS

SEVERITY={'low':25,'moderate':50,'high':75,'critical':100}
LIKELIHOOD={'low':25,'moderate':50,'high':100}
DOMAIN_WEIGHTS={domain['id']:domain['weight'] for domain in DOMAINS}
SCORING_METHOD={
    'version':'risk-review-priority-v1','name':'근거 기반 위험 검토 우선순위',
    'formula':'0.60 × 현재 위협 등급 지수 + 0.25 × 미래 가능성 관심 지수 + 0.15 × 영향 분야 범위 지수',
    'severity_scale':SEVERITY,'future_attention_scale':LIKELIHOOD,
    'domain_weights':DOMAIN_WEIGHTS,'domain_formula':'min(60, 고유 영향 분야 가중치 합) / 60 × 100',
    'weights':{'current':.60,'future_attention':.25,'domain_scope':.15},
    'unknown_policy':'미확인 성분은 0점으로 확정하지 않고 0~100 범위로 남깁니다. 모든 필수 성분이 알려진 경우만 단일 점수를 제공합니다.',
    'limitations':['숫자는 순서 비교용 정책 지수이며 발생 확률·피해액·세계 전체 위협 점수가 아닙니다.',
                   '미래 지수는 조건부 가능성에 대한 관심이며 미래 피해 규모가 아닙니다. 현재 피해 등급을 미래 피해로 대입하지 않습니다.',
                   '영향 분야는 범위의 우선순위이며 실제 피해 규모를 입증하지 않습니다.',
                   '근거 수는 별도 완전성 지표이며 점수에 곱하지 않습니다. 증거 부족으로 심각한 위협의 중요도를 낮추지 않습니다.',
                   '부분 점수는 단일 순위로 확정하지 않습니다. 기본 정렬은 알려진 기여의 하한순이며 미확인·재검토 목록을 별도 필터로 확인할 수 있습니다.']}


def priority_score(risk):
    current=SEVERITY.get(risk.get('current_severity'))
    future=LIKELIHOOD.get(risk.get('future_likelihood'))
    if risk.get('source_status') not in (None,'matched_current'):
        current=future=None
    elif risk.get('date_status') in ('stale','unknown','future_dated'):
        current=None
    domains=set(risk.get('impact_domains') or [])&DOMAIN_WEIGHTS.keys()
    domain=round(min(60,sum(DOMAIN_WEIGHTS[key] for key in domains))/60*100,2)
    domain_known=bool(domains)
    pieces=[(current,.60),(future,.25),(domain if domain_known else None,.15)]
    low=round(sum((value if value is not None else 0)*weight for value,weight in pieces),2)
    high=round(sum((value if value is not None else 100)*weight for value,weight in pieces),2)
    complete=all(value is not None for value,_ in pieces)
    state='scored' if complete else 'unknown' if current is None and future is None else 'partial'
    if risk.get('source_status') not in (None,'matched_current') or risk.get('date_status') in ('stale','unknown','future_dated'):
        state='needs_review'
    return {'score':round(low,2) if complete else None,'range':[low,high],'status':state,
            'current_score':current,'future_attention_score':future,'domain_score':domain if domain_known else None,
            'components':{'current':{'value':current,'weight':.6},'future_attention':{'value':future,'weight':.25},
                          'domain_scope':{'value':domain if domain_known else None,'weight':.15}},
            'explanation':'미확인은 범위로 유지합니다. 미래 가능성 관심 지수는 확률도 미래 피해 규모도 아닙니다.'}


def _all_risks(db,current_time=None):
    today=(current_time or datetime.now(timezone.utc)).date().isoformat()
    token=revision_token(db)
    def build():
        base=read_risks(db,current_time=current_time,unlimited=True,include_source_evidence=True)
        flat=[e for risk in base['risks'] for e in risk.get('raw_evidence',[])]
        from risk_source_state import evidence_source_states,evidence_index_key
        states=evidence_source_states(db,flat)
        values=[];duplicates={}
        risk_keys=set(RISK_SCHEMA['properties']['risks']['items']['properties'])-{'evidence_ids'}
        for original in base['risks']:
            risk=dict(original)
            raw=risk.pop('raw_evidence',[])
            checks=[dict(states.get(evidence_index_key(e),{'status':'unverifiable','reason':'현재 원문 대조 결과 없음'}),evidence_id=e.get('id')) for e in raw]
            statuses={entry['status'] for entry in checks}
            source_status=('changed' if 'changed' in statuses else 'unavailable' if 'unavailable' in statuses else
                           'unverifiable' if not checks or 'unverifiable' in statuses else 'matched_current')
            risk['source_status']=source_status;risk['source_checks']=checks
            article_dates=[]
            for check in checks:
                for value in check.get('source_dates',[]):
                    try:article_dates.append(date.fromisoformat(value))
                    except (ValueError,TypeError):pass
            latest=max(article_dates) if article_dates else None
            age=(date.fromisoformat(today)-latest).days if latest else None
            risk['archive_evidence_latest_date']=risk.get('evidence_latest_date','')
            risk['evidence_latest_date']=latest.isoformat() if latest else ''
            risk['source_age_days']=age
            risk['date_status']='unknown' if age is None else 'future_dated' if age<0 else 'stale' if age>30 else 'recent'
            risk['date_basis']='article_label' if latest else 'unknown'
            risk['stale']=age>30 if age is not None else None
            risk['current_severity']=risk['assessed_current_severity'] if age is not None and 0<=age<=30 else 'unknown'
            risk['assessed_future_likelihood']=risk['future_likelihood']
            if source_status!='matched_current':
                risk['current_severity']='unknown';risk['future_likelihood']='unknown'
            risk['priority']=priority_score(risk)
            risk['status']=risk['priority']['status']
            risk['evidence_count']=len(risk['evidence'])
            risk['observed_indicator_count']=len(risk.get('observed_indicators',[]))
            risk['evidence_completeness']={'cited':len(checks),'matched_current':sum(entry['status']=='matched_current' for entry in checks),
                                           'observed_indicators':risk['observed_indicator_count'],'independent_truth_confirmed':False}
            signature=_digest([{key:original.get(key) for key in sorted(risk_keys)},risk.get('assessment_report_hash'),
                               sorted((_digest([e.get('origin'),e.get('url'),e.get('title'),e.get('text')]) for e in raw))])
            history={'id':risk['id'],'workflow_run_id':risk['workflow_run_id'],'analysis_at':risk['analysis_at']}
            if signature in duplicates:
                duplicates[signature]['run_history'].append(history)
                continue
            risk['run_history']=[history];duplicates[signature]=risk;values.append(risk)
        return {'risks':values,'coverage':dict(base['coverage'],risk_records=len(base['risks']),unique_risks=len(values),
                    duplicate_records=len(base['risks'])-len(values),source_review_required=sum(r['source_status']!='matched_current' for r in values)),
                'needs_review':base['needs_review'],'limitations':base['limitations']}
    return _apply_resolutions(db,cached_read(db,'risk-page-v1',(token,today) if token is not None else None,build))



def _replacement_verified(db, resolution):
    """A resolution pointer is not evidence; admit its replacement independently."""
    import json
    from link_groups import canonical_url
    from recursive_improvement import quality_metrics
    from risk_analysis import validated_risk_content
    from risk_source_state import evidence_source_states,evidence_index_key
    identity=resolution.get('replacement_workflow_run_id')
    url=canonical_url(resolution.get('corrected_source_url') or '')
    if not identity or not url:return False
    row=db.execute('SELECT status,error FROM strategic_workflow_runs WHERE id=?',(identity,)).fetchone()
    final=db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id=? AND stage='final'",(identity,)).fetchone()
    if not row or not final:return False
    try:payload=json.loads(final[0])
    except (ValueError,TypeError):return False
    run={'id':identity,'status':row[0],'error':row[1],'results':payload}
    if not quality_metrics(run)['verified'] or not validated_risk_content(payload,identity):return False
    evidence=[e for e in payload.get('evidence',[]) if canonical_url(e.get('url') or '')==url]
    # Newly available or withdrawn URL text invalidates a Telegram-only or older
    # replacement. A correction pointer cannot claim current-source review until
    # the actual current excerpt was part of the independent audit.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_excerpts'").fetchone():
        cached=db.execute('SELECT result_json FROM source_excerpts WHERE canonical_url=?',(url,)).fetchone()
        try:source=json.loads(cached[0]) if cached else {}
        except (ValueError,TypeError):return False
        current_text=str(source.get('text') or '')[:3500] if source.get('status')=='fetched' else ''
        retrieved=[e for e in evidence if e.get('origin')=='fetched_url_excerpt']
        if bool(current_text)!=bool(retrieved) or any(e.get('text')!=current_text for e in retrieved):return False
    ids={e.get('id') for e in evidence}
    cited={ref for claim in payload.get('report',{}).get('claims',[]) for ref in claim.get('evidence_ids',[])}
    risk=payload.get('risk_report') or {}
    reviewed=(set(risk.get('assessed_evidence_ids') or [])|set(risk.get('not_assessable_evidence_ids') or [])) & set((payload.get('risk_verification') or {}).get('checked_evidence_ids') or [])
    if not ids & cited or not ids & reviewed:return False
    relevant=[e for e in evidence if e.get('id') in (cited|reviewed)]
    states=evidence_source_states(db,relevant)
    return bool(relevant) and all(states.get(evidence_index_key(e),{}).get('status')=='matched_current' for e in relevant)


def _apply_resolutions(db,data):
    """Read explicit human/operator corrections; never create or silently resolve them."""
    import copy
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='risk_review_resolutions'").fetchone()
    records={}
    if exists:
        for row in db.execute('SELECT risk_id,status,reason,corrected_source_url,replacement_workflow_run_id,created_at FROM risk_review_resolutions ORDER BY created_at'):
            records[row[0]]=dict(zip(('risk_id','status','reason','corrected_source_url','replacement_workflow_run_id','created_at'),row))
    result=copy.deepcopy(data);withdrawn=superseded=resolved=pending=history_total=0
    for risk in result['risks']:
        aliases={risk['id'],*(entry['id'] for entry in risk.get('run_history',[]))}
        rows=[record for key,record in records.items() if key in aliases]
        record=max(rows,key=lambda row:row['created_at']) if rows else None
        risk['lifecycle']='active'
        if not record:continue
        record=dict(record);history_total+=1
        # Only an explicit, reasoned withdrawal leaves the active ranking.
        if record['status'] not in ('withdrawn_source_mismatch','source_correction','context_reassessment') or not str(record['reason'] or '').strip():
            record['verification_status']='pending_resolution'
            risk['resolution']=record;pending+=1
            continue
        if record['status']=='withdrawn_source_mismatch':
            risk['lifecycle']='withdrawn_source_mismatch';withdrawn+=1
        accepted=_replacement_verified(db,record)
        if accepted and record['status'] in ('source_correction','context_reassessment'):
            risk['lifecycle']='superseded';superseded+=1
        record['verification_status']='resolved' if accepted else 'pending_resolution'
        resolved+=int(accepted);pending+=int(not accepted)
        risk['resolution']=record
    result['coverage'].update(withdrawn=withdrawn,superseded=superseded,resolved=resolved,resolution_pending=pending,
        history_total=history_total,active_risks=len(result['risks'])-withdrawn-superseded,
        source_review_archive_total=sum(r['source_status']!='matched_current' for r in result['risks']),
        historical_source_review_required=sum(r['source_status']!='matched_current' and r['lifecycle']!='active' for r in result['risks']),
        source_review_required=sum(r['source_status']!='matched_current' and r['lifecycle']=='active' for r in result['risks']))
    return result


def _one(params,key,default=''):
    value=params.get(key,default)
    return str(value[0] if isinstance(value,list) and value else value or default)


def read_risk_page(db,params=None,*,current_time=None):
    params=params or {}
    try:
        page=max(1,int(_one(params,'page','1')));size=max(1,min(48,int(_one(params,'page_size','12'))))
        review_page=max(1,int(_one(params,'pending_page',_one(params,'review_page','1'))));review_size=max(1,min(48,int(_one(params,'pending_page_size',_one(params,'review_page_size','12')))))
    except ValueError:raise ValueError('페이지와 페이지 크기는 정수여야 합니다.') from None
    data=_all_risks(db,current_time)
    items=data['risks']
    query=_one(params,'q').casefold().strip();domain=_one(params,'domain');severity=_one(params,'severity');likelihood=_one(params,'likelihood');status=_one(params,'status')
    domain='' if domain=='all' else domain;severity='' if severity=='all' else severity
    likelihood='' if likelihood=='all' else likelihood;status='' if status=='all' else status
    if domain and domain not in DOMAIN_WEIGHTS:raise ValueError('알 수 없는 영향 분야입니다.')
    if severity and severity not in {*SEVERITY,'unknown'}:raise ValueError('알 수 없는 현재 위험 등급입니다.')
    if likelihood and likelihood not in {*LIKELIHOOD,'unknown'}:raise ValueError('알 수 없는 미래 가능성입니다.')
    if status and status not in {'scored','partial','unknown','needs_review','withdrawn','history','resolved','superseded'}:raise ValueError('알 수 없는 검토 상태입니다.')
    if status in ('withdrawn','history','resolved','superseded'):
        items=[risk for risk in items if (status=='withdrawn' and risk.get('lifecycle')=='withdrawn_source_mismatch') or (status=='superseded' and risk.get('lifecycle')=='superseded') or (status=='history' and risk.get('resolution')) or (status=='resolved' and risk.get('resolution',{}).get('verification_status')=='resolved')]
        status=''
    else:
        items=[risk for risk in items if risk.get('lifecycle')=='active']
    items=[risk for risk in items if (not query or query in ' '.join(str(risk.get(key,'')) for key in ('title','current_basis','scenario','uncertainty')).casefold())
        and (not domain or domain in risk['impact_domains']) and (not severity or risk['current_severity']==severity)
        and (not likelihood or risk['future_likelihood']==likelihood) and (not status or risk['status']==status)]
    distributions={key:dict(Counter(risk[key] for risk in items)) for key in ('current_severity','future_likelihood','status','source_status')}
    sort=_one(params,'sort','priority')
    if sort=='recent':items.sort(key=lambda risk:(risk['analysis_at'],risk['id']),reverse=True)
    elif sort=='priority':items.sort(key=lambda risk:(risk['priority']['range'][0],risk['priority']['range'][1],risk['id']),reverse=True)
    elif sort=='review':items.sort(key=lambda risk:(risk['status']=='needs_review',risk['priority']['range'][1],risk['id']),reverse=True)
    elif sort=='current':items.sort(key=lambda risk:(risk['priority']['current_score'] if risk['priority']['current_score'] is not None else -1,risk['id']),reverse=True)
    elif sort=='future':items.sort(key=lambda risk:(risk['priority']['future_attention_score'] if risk['priority']['future_attention_score'] is not None else -1,risk['id']),reverse=True)
    else:raise ValueError('알 수 없는 정렬입니다.')
    total=len(items);pages=max(1,math.ceil(total/size));page=min(page,pages)
    review_total=len(data['needs_review']);review_pages=max(1,math.ceil(review_total/review_size));review_page=min(review_page,review_pages)
    fields=('id','title','current_severity','future_likelihood','assessed_current_severity','assessed_future_likelihood',
            'impact_domains','analysis_at','evidence_latest_date','stale','date_status','source_age_days','status','source_status','horizon',
            'date_basis','evidence_count','observed_indicator_count','uncertainty','workflow_run_id','priority','evidence_completeness','lifecycle','resolution')
    return {'items':[{key:risk.get(key) for key in fields} for risk in items[(page-1)*size:page*size]],
            'pagination':{'page':page,'page_size':size,'total':total,'total_pages':pages},'total':total,
            'coverage':data['coverage'],'distributions':distributions,'scoring_method':SCORING_METHOD,
            'needs_review':data['needs_review'][(review_page-1)*review_size:review_page*review_size],
            'review_pagination':{'page':review_page,'page_size':review_size,'total':review_total,'total_pages':review_pages},
            'limitations':data['limitations']}


def read_risk_detail(db,id,*,current_time=None):
    for risk in _all_risks(db,current_time)['risks']:
        if risk['id']==id or any(history['id']==id for history in risk['run_history']):
            return {'risk':risk,'scoring_method':SCORING_METHOD}
    return None
