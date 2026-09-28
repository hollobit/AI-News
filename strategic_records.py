"""Versioned analyst decisions, assumptions and evidence-linked research milestones."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def init_records(db):
    db.execute('''CREATE TABLE IF NOT EXISTS intel_records (
        id TEXT PRIMARY KEY,kind TEXT NOT NULL,version INTEGER NOT NULL,
        payload_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS intel_records_kind ON intel_records(kind,updated_at)')
    db.execute('''CREATE TABLE IF NOT EXISTS intel_record_history (
        id TEXT NOT NULL,version INTEGER NOT NULL,reason TEXT NOT NULL,
        payload_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(id,version))''')
    db.execute('''CREATE TABLE IF NOT EXISTS intel_risk_history (
        identity TEXT NOT NULL,fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL,
        cause TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(identity,fingerprint))''')


def text(value, name, required=False, limit=3000):
    if not isinstance(value, str) or len(value)>limit or (required and not value.strip()):
        raise ValueError(f'{name} 입력을 확인해 주세요.')
    return value.strip()


def strings(value, name, maximum=30, limit=1000):
    if not isinstance(value, list) or len(value)>maximum:
        raise ValueError(f'{name}은 최대 {maximum}개 목록이어야 합니다.')
    return list(dict.fromkeys(text(v,name,True,limit) for v in value))


def date_value(value, name):
    value=text(value or '',name,limit=100)
    if value:
        try: datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError: raise ValueError(f'{name}은 날짜 또는 ISO 시각이어야 합니다.') from None
    return value


def get_record(db, identity, kind=None):
    row=db.execute('SELECT payload_json FROM intel_records WHERE id=?',(identity,)).fetchone()
    item=json.loads(row[0]) if row else None
    if kind and item and item['kind']!=kind:
        return None
    return item


def record_history(db, identity):
    return [dict(json.loads(r[0]),change_reason=r[1],recorded_at=r[2]) for r in db.execute(
        'SELECT payload_json,reason,created_at FROM intel_record_history WHERE id=? ORDER BY version DESC',(identity,))]


def save_record(db, kind, value, reason, identity=None):
    old=get_record(db,identity,kind) if identity else None
    if identity and not old:
        raise ValueError('수정할 기록을 찾을 수 없습니다.')
    identity=identity or kind+':'+uuid4().hex
    value=dict(value,id=identity,kind=kind,version=(old or {}).get('version',0)+1,
               created_at=(old or {}).get('created_at',now()),updated_at=now())
    db.execute('INSERT INTO intel_records VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
               'version=excluded.version,payload_json=excluded.payload_json,updated_at=excluded.updated_at',
               (identity,kind,value['version'],json.dumps(value,ensure_ascii=False),value['created_at'],value['updated_at']))
    db.execute('INSERT INTO intel_record_history VALUES (?,?,?,?,?)',
               (identity,value['version'],reason,json.dumps(value,ensure_ascii=False),now()))
    return value


def list_records(db, kind, params=None):
    params=params or {}
    def one(key,default=''):
        value=params.get(key,default)
        return value[0] if isinstance(value,list) and value else value
    page=max(1,int(one('page',1)));size=min(50,max(1,int(one('page_size',12))))
    query=str(one('q')).casefold();status=str(one('status'))
    items=[json.loads(r[0]) for r in db.execute('SELECT payload_json FROM intel_records WHERE kind=? ORDER BY updated_at DESC,id',(kind,))]
    if query:items=[v for v in items if query in json.dumps(v,ensure_ascii=False).casefold()]
    if status:items=[v for v in items if v.get('status')==status or v.get('review_state')==status]
    total=len(items)
    return {'items':items[(page-1)*size:page*size],'total':total,'page':page,'page_size':size,'total_pages':max(1,math.ceil(total/size))}


def validate_refs(refs, document_lookup):
    refs=strings(refs or [],'근거 문서',maximum=50,limit=500)
    for ref in refs:
        item=document_lookup(ref)
        if not item or item.get('status','current')!='current':
            raise ValueError('현재 보관 문서에 없는 근거 ID가 있습니다.')
    return refs


def evidence_state(refs, document_lookup):
    rows=[]
    for ref in refs:
        item=document_lookup(ref)
        rows.append({'id':ref,'version':(item or {}).get('version'),
                     'hash':(item or {}).get('content_hash') or (item or {}).get('source_hash') or (item or {}).get('input_hash'),
                     'status':(item or {}).get('status','missing')})
    return {'fingerprint':digest(rows),'documents':rows}


def decision_value(payload, document_lookup, old=None):
    value=dict(old or {});value.update(payload)
    result={key:text(value.get(key,''),key,key in ('title','question'),1000 if key in ('title','owner') else 4000)
            for key in ('title','question','topic_id','rationale','hypothesis','owner','selected_option_id')}
    result['review_at']=date_value(value.get('review_at',''),'재검토일')
    status=value.get('status','proposed')
    if status not in {'proposed','active','revised','withdrawn'}:raise ValueError('결정 상태를 확인해 주세요.')
    options=value.get('options',[])
    if not isinstance(options,list) or len(options)>8:raise ValueError('선택지는 최대 8개입니다.')
    result['options']=[];seen=set()
    for i,option in enumerate(options):
        if not isinstance(option,dict):raise ValueError('선택지 형식을 확인해 주세요.')
        identity=text(option.get('id') or f'option-{i+1}','선택지 ID',True,100)
        if identity in seen:raise ValueError('선택지 ID가 중복됩니다.')
        seen.add(identity)
        result['options'].append(dict(id=identity,**{k:text(option.get(k,''),k,k=='label',2500)
            for k in ('label','benefits','costs','prerequisites','counter_evidence','uncertainties')}))
    if result['selected_option_id'] and result['selected_option_id'] not in seen:
        raise ValueError('선택한 대안이 목록에 없습니다.')
    if result['selected_option_id'] and not result['rationale']:
        raise ValueError('대안을 선택한 이유를 기록해 주세요.')
    result.update(status=status,evidence_ids=validate_refs(value.get('evidence_ids',[]),document_lookup),
                  support_conditions=strings(value.get('support_conditions',[]),'지지 조건'),
                  refutation_conditions=strings(value.get('refutation_conditions',[]),'반박 조건'))
    result['evidence_state']=evidence_state(result['evidence_ids'],document_lookup)
    result['review_state']='current' if result['evidence_ids'] else 'evidence_needed'
    result['basis']='사용자가 기록한 전략 판단과 가설; 자동 사실 검증 결과가 아님'
    return result


def scenario_value(payload,document_lookup,old=None):
    value=dict(old or {});value.update(payload)
    result={key:text(value.get(key,''),key,key=='title',2000) for key in ('title','topic_id','decision_id')}
    assumptions=value.get('assumptions',[]);conditions=value.get('conditions',[])
    if not isinstance(assumptions,list) or not 1<=len(assumptions)<=12:
        raise ValueError('시나리오 가정은 1~12개가 필요합니다.')
    if not isinstance(conditions,list) or len(conditions)>20:raise ValueError('관측 조건은 최대 20개입니다.')
    result['assumptions']=[];result['conditions']=[]
    for assumption in assumptions:
        if not isinstance(assumption,dict):raise ValueError('가정 형식을 확인해 주세요.')
        state=assumption.get('status','hypothetical')
        if state not in {'hypothetical','unknown','proposed','enforced','suspended','negated'}:
            raise ValueError('가정 상태를 확인해 주세요.')
        result['assumptions'].append(dict(status=state,evidence_ids=validate_refs(assumption.get('evidence_ids',[]),document_lookup),
            **{key:text(assumption.get(key,''),key,key in ('name','value'),2000) for key in ('name','value','basis')}))
    for condition in conditions:
        if not isinstance(condition,dict):raise ValueError('관측 조건 형식을 확인해 주세요.')
        state=condition.get('state','unknown')
        if state not in {'unknown','observed','not_observed','refuted','proposed','enforced','suspended'}:
            raise ValueError('조건 상태를 확인해 주세요.')
        result['conditions'].append({'label':text(condition.get('label',''),'관측 조건',True,600),
            'state':state,'state_origin':'user','next_review_at':date_value(condition.get('next_review_at',''),'다음 검토일')})
    refs=validate_refs(value.get('evidence_ids',[]),document_lookup)
    result['evidence_ids']=list(dict.fromkeys(refs+[ref for a in result['assumptions'] for ref in a['evidence_ids']]))
    result['evidence_state']=evidence_state(result['evidence_ids'],document_lookup)
    result.update(status='draft',review_state='current' if result['evidence_ids'] else 'evidence_needed',
                  basis='조건부 가정의 비교; 발생 확률 또는 실제 정책 시행의 확인이 아님')
    return result


def refresh_record(db,item,document_lookup,topic_documents=None):
    """Changed evidence prompts a decision review; it never silently changes the decision."""
    state=evidence_state(item.get('evidence_ids',[]),document_lookup)
    related=sorted(topic_documents or [])
    related_hash=digest(related)
    review_due=bool(item.get('review_at') and item['review_at'][:10]<=now()[:10])
    changed=state['fingerprint']!=(item.get('evidence_state') or {}).get('fingerprint')
    new_related=bool(item.get('related_documents_hash') and item['related_documents_hash']!=related_hash)
    conditions=[]
    documents=[document_lookup(ref) for ref in list(dict.fromkeys(item.get('evidence_ids',[])+related))[:80]]
    documents=[d for d in documents if d]
    terms=(item.get('support_conditions',[])+item.get('refutation_conditions',[]) if item['kind']=='decision'
           else [c['label'] for c in item.get('conditions',[])])
    for label in terms:
        # Exact phrase observation is a search lead, not confirmation of a condition.
        matches=[d['id'] for d in documents if label.casefold() in
                 (' '.join(str(d.get(k) or '') for k in ('title','text','summary'))).casefold()]
        conditions.append({'label':label,'state':'mentioned' if matches else 'not_found_in_scope',
                           'evidence_ids':matches,'basis':'문구 관측; 조건 충족 판단은 검토 필요'})
    value=dict(item,evidence_state=state,related_documents_hash=related_hash,
               condition_observations=conditions,review_state='needs_review' if changed or new_related or review_due
               else item.get('review_state','current'))
    substantive={k:v for k,v in value.items() if k not in ('id','kind','version','created_at','updated_at')}
    before={k:v for k,v in item.items() if k not in ('id','kind','version','created_at','updated_at')}
    if digest(substantive)==digest(before):return item
    causes=[name for yes,name in [(changed,'원문 또는 근거 상태 변경'),(new_related,'관련 주제 자료 변경'),(review_due,'검토일 도래')] if yes]
    value['last_observed_at']=now()
    return save_record(db,item['kind'],value,' / '.join(causes) or '관측 조건 및 관련 자료 연결',item['id'])
