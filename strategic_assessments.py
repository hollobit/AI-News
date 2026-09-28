"""Separate editorial importance, reviewed risk, evidence coverage and opportunities."""
from collections import defaultdict
from datetime import date
import json
import math
import re

from link_groups import canonical_url
from strategic_records import digest, now, save_record, get_record
from strategic_value import evaluate_news, DOMAINS
from copy import deepcopy

VERSION='strategic-assessment-v2'
DEFAULT_WEIGHTS={'importance':.4,'risk':.3,'evidence':.1,'opportunity':.2}


def init_assessments(db):
    db.execute('''CREATE TABLE IF NOT EXISTS intel_assessments (
        document_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL)''')


def profile_value(payload):
    name=payload.get('name','')
    if not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('평가 프로필 이름을 확인해 주세요.')
    weights=payload.get('weights',DEFAULT_WEIGHTS)
    if not isinstance(weights,dict) or set(weights)!=set(DEFAULT_WEIGHTS):raise ValueError('네 가지 평가 가중치를 입력해 주세요.')
    def numeric(value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100:
            raise ValueError('가중치는 0~100의 유한한 수여야 합니다.')
        return float(value)
    weights={key:numeric(value) for key,value in weights.items()}
    total=sum(weights.values())
    if not total:raise ValueError('하나 이상의 가중치가 0보다 커야 합니다.')
    domains=payload.get('domain_weights') or {d['id']:d['weight'] for d in DOMAINS}
    if not isinstance(domains,dict) or set(domains)-{d['id'] for d in DOMAINS}:
        raise ValueError('영향 분야 가중치를 확인해 주세요.')
    domains={d['id']:numeric(domains.get(d['id'],d['weight'])) for d in DOMAINS}
    return {'name':name.strip(),'title':name.strip(),'weights':{k:v/total for k,v in weights.items()},
            'domain_weights':domains,'method_version':VERSION,'status':'active',
            'basis':'검토 순서용 사용자 가중치; 사실성 또는 발생 확률을 변경하지 않음'}


def default_profile():
    return dict(profile_value({'name':'균형 검토'}),id='default',version=1)


def apply_profile(assessment,profile):
    value=deepcopy(assessment)
    matched=value['score_details']['importance']['matched']
    importance=min(100,(15 if matched['country_terms'] else 0)+(25 if matched['government_terms'] else 0)
        +min(60,sum(profile['domain_weights'].get(d['id'],d['weight']) for d in matched['domains'])))
    value['scores']['importance']=importance
    if value.get('opportunities'):value['scores']['opportunity']=round((importance+value['scores']['evidence'])/2,1)
    ranges={key:([v,v] if v is not None else [0,100]) for key,v in value['scores'].items()}
    ranges['risk']=value['score_details']['risk']['range']
    weighted=[round(sum(profile['weights'][k]*ranges[k][i] for k in ranges),2) for i in (0,1)]
    value['priority']={'score':weighted[0] if weighted[0]==weighted[1] else None,'range':weighted,
        'profile_id':profile['id'],'profile_version':profile['version'],'method_version':VERSION}
    return value


def policy_observations(doc):
    text='\n'.join(str(doc.get(k) or '') for k in ('title','text','source_text'))
    cues={
        'proposal':r'법안|정책 제안|입법예고|propos(?:al|ed)|draft law|法案|征求意见',
        'announcement':r'공식 발표|발표했|announc(?:e|ed|ement)|official statement|正式发布',
        'budget_procurement':r'예산|공공조달|정부 계약|budget|procurement|预算|采购',
        'implementation':r'시행|집행|발효|entered into force|implemented|生效|实施',
        'suspended':r'유예|중단|철회|suspend(?:ed)?|withdrawn|暂缓|撤回'}
    result=[]
    for sentence in re.split(r'(?<=[.!?。])\s*|\n',text):
        for stage,pattern in cues.items():
            match=re.search(pattern,sentence,re.I)
            if match:
                conditional=bool(re.search(r'아니|않|예정|가능|검토|\b(?:not|if|may|could|would)\b|未|拟',sentence,re.I))
                result.append({'stage':stage,'cue':match.group(),'quote':sentence[:500],
                    'status':'conditional_or_negated_mention' if conditional else 'reported_mention',
                    'document_id':doc['id'],'basis':'원문 표현 관측; 정책 시행의 독립 확인 아님'})
    return result[:12]


def score_document(doc,claims,risks,profile=None):
    profile=profile or default_profile()
    priority=evaluate_news({'title':doc.get('title'),'text':doc.get('text'),
        'source_context':{'status':doc.get('source_status'),'title':doc.get('source_title'),'text':doc.get('source_text')}})
    impact=min(60,sum(profile['domain_weights'].get(d['id'],d['weight']) for d in priority['domains']))
    importance=min(100,(15 if priority['country_terms'] else 0)+(25 if priority['government_terms'] else 0)+impact)
    current=[c for c in claims if c.get('status') in ('current','current_reviewed','verified','accepted')]
    opportunity_claims=[c for c in current if c.get('category')=='opportunity']
    has_source=doc.get('source_status')=='fetched' and bool(doc.get('source_text'))
    has_date=bool(doc.get('published_day'))
    independent=doc.get('independent_confirmation')=='independent'
    # This measures visible checks, never factual probability or authority.
    checks={'retrieved_source':25 if has_source else 0,'current_reviewed_claim':45 if current else 0,
            'article_date':10 if has_date else 0,'reviewed_independence':20 if independent else 0}
    evidence=sum(checks.values())
    active=[risk for risk in risks if risk.get('lifecycle','active')=='active']
    risk_values=[r.get('priority',{}).get('score') for r in active]
    known=[v for v in risk_values if isinstance(v,(int,float))]
    risk=max(known) if known and len(known)==len(active) else None
    risk_ranges=[r.get('priority',{}).get('range',[0,100]) for r in active]
    risk_range=[max(r[0] for r in risk_ranges),max(r[1] for r in risk_ranges)] if risk_ranges else [0,100]
    opportunity=round((importance+evidence)/2,1) if opportunity_claims else None
    scores={'importance':importance,'risk':risk,'evidence':evidence,'opportunity':opportunity}
    ranges={k:([v,v] if v is not None else [0,100]) for k,v in scores.items()}
    ranges['risk']=risk_range
    weighted=[round(sum(profile['weights'][k]*ranges[k][i] for k in scores),2) for i in (0,1)]
    return {'id':doc['id'],'scores':scores,'score_details':{
        'importance':{'basis':'국가·정부 언급 및 고영향 분야 관련성','matched':priority},
        'risk':{'range':risk_range,'risk_ids':[r['id'] for r in active],'basis':'현재 원문과 일치하는 독립 검토 위험 등급; 미상 유지'},
        'evidence':{'checks':checks,'independence':doc.get('independent_confirmation','unknown'),
                    'basis':'확보·검토 정보의 충족도이며 참일 확률이 아님'},
        'opportunity':{'claim_ids':[c['id'] for c in opportunity_claims],
                      'basis':'검토된 기회 해석의 검토 지수; 수익·성공 확률이 아님'}},
        'priority':{'score':weighted[0] if weighted[0]==weighted[1] else None,'range':weighted,
                    'profile_id':profile['id'],'profile_version':profile['version'],'method_version':VERSION},
        'opportunities':[{k:c.get(k) for k in ('id','title','detail','uncertainty','document_ids')} for c in opportunity_claims],
        'policy_observations':policy_observations(doc),'countries':{'mentioned':priority['country_terms'],
            'actors':doc.get('actor_countries',[]),'affected':doc.get('affected_countries',[]),
            'basis':'언급 국가와 조치·피영향 국가는 별도 근거가 필요함'},
        'domains':priority['domains'],'method_version':VERSION}


def sync_assessments(db,documents,claims,risks):
    by_doc=defaultdict(list);by_url=defaultdict(list);by_snapshot=defaultdict(list)
    for claim in claims:
        for identity in claim.get('document_ids',[]):by_doc[identity].append(claim)
    for risk in risks:
        for source in risk.get('evidence',[]):
            url=canonical_url(source.get('source_url') or source.get('url') or '')
            if url and risk not in by_url[url]:by_url[url].append(risk)
            elif not url and source.get('text'):
                key=(str(source.get('title') or '')[:300],source['text'])
                if risk not in by_snapshot[key]:by_snapshot[key].append(risk)
    changes=0
    existing=dict(db.execute('SELECT document_id,fingerprint FROM intel_assessments'))
    for doc in documents:
        url=canonical_url(doc.get('url') or doc.get('source_url') or '')
        matched=by_url[url] if url else by_snapshot[(doc.get('title','')[:300],doc.get('snapshot_text',''))]
        fingerprint=digest([VERSION,doc,by_doc[doc['id']],matched])
        if existing.get(doc['id'])==fingerprint:continue
        value=score_document(doc,by_doc[doc['id']],matched)
        value.update(document_version=doc.get('version'),updated_at=now())
        db.execute('INSERT OR REPLACE INTO intel_assessments VALUES (?,?,?)',
                   (doc['id'],fingerprint,json.dumps(value,ensure_ascii=False)))
        changes+=1
    return changes


def risk_timeline(db,risks):
    from risk_views import SCORING_METHOD
    changed=0
    for risk in risks:
        urls=sorted({canonical_url(e.get('source_url') or '') for e in risk.get('evidence',[])})
        identity='risk-thread:'+digest([risk['title'],risk.get('scenario'),urls])[:24]
        value={k:risk.get(k) for k in ('id','title','current_severity','future_likelihood','horizon','source_status',
            'date_status','priority','lifecycle','resolution','observed_indicators','escalation_signals','counter_evidence','uncertainty',
            'current_basis','scenario','assumptions','affected_assets','affected_actors','mitigations','impact_domains','workflow_run_id')}
        value.update(identity=identity,source_urls=urls,scoring_version=SCORING_METHOD['version'],analysis_at=risk.get('analysis_at'))
        fingerprint=digest(value)
        old=db.execute('SELECT payload_json FROM intel_risk_history WHERE identity=? ORDER BY created_at DESC LIMIT 1',(identity,)).fetchone()
        previous=json.loads(old[0]) if old else None
        cause=('initial_observation' if not previous else 'source_change' if any(previous.get(k)!=value.get(k) for k in ('source_status','resolution','lifecycle'))
               else 'method_change' if previous.get('scoring_version')!=SCORING_METHOD['version'] else 'assessment_change')
        cursor=db.execute('INSERT OR IGNORE INTO intel_risk_history VALUES (?,?,?,?,?)',
                          (identity,fingerprint,json.dumps(value,ensure_ascii=False),cause,now()))
        changed+=cursor.rowcount
    return changed
