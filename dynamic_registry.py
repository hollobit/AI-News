"""Versioned topic/signal registry. Exclusion is a durable, reversible tombstone.

Automatic sync expects the complete candidate set for this registry, not a UI filter.
Public mutations commit atomically; use a dedicated connection without pending writes.
No model, network, or evidence-verification claims are performed here.
"""
import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone


def _json(value):
    try:
        text=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
    except (TypeError,ValueError) as error:
        raise ValueError('메타데이터는 유효한 JSON이어야 합니다.') from error
    if len(text.encode())>32768:raise ValueError('메타데이터는 32KB 이하여야 합니다.')
    return text


def _text(value,name,limit,required=True):
    if not isinstance(value,str):raise ValueError(f'{name}은 문자열이어야 합니다.')
    value=unicodedata.normalize('NFKC',value).strip()
    if len(value)>limit or (required and not value) or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValueError(f'{name} 길이 또는 문자가 올바르지 않습니다.')
    return value


def _validate(payload):
    if not isinstance(payload,dict):raise ValueError('항목은 객체여야 합니다.')
    kind=payload.get('kind','topic')
    if kind not in ('topic','signal'):raise ValueError('종류는 topic 또는 signal이어야 합니다.')
    label=_text(payload.get('label',payload.get('name','')),'이름',100)
    terms=payload.get('terms',[label])
    if not isinstance(terms,list) or not 1<=len(terms)<=32:raise ValueError('검색어는 1~32개여야 합니다.')
    terms=list(dict.fromkeys(_text(term,'검색어',100) for term in terms))
    description=_text(payload.get('description',''),'설명',2000,False)
    metadata=payload.get('metadata',{})
    if not isinstance(metadata,dict):raise ValueError('메타데이터는 객체여야 합니다.')
    _json(metadata)
    return {'kind':kind,'label':label,'terms':terms,'description':description,'metadata':metadata}


def init_registry(db):
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS dynamic_topic_registry (
            id TEXT PRIMARY KEY,kind TEXT NOT NULL,label TEXT NOT NULL,terms_json TEXT NOT NULL,
            description TEXT NOT NULL,origin TEXT NOT NULL,excluded INTEGER NOT NULL,status TEXT NOT NULL,
            metadata_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,version INTEGER NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS dynamic_topic_registry_history (
            version INTEGER PRIMARY KEY AUTOINCREMENT,item_id TEXT NOT NULL,action TEXT NOT NULL,
            payload_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


def _rows(db):
    cursor=db.execute('SELECT * FROM dynamic_topic_registry ORDER BY created_at,id')
    columns=[column[0] for column in cursor.description]
    result=[]
    for row in cursor:
        value=dict(zip(columns,row));value['terms']=json.loads(value.pop('terms_json'))
        value['metadata']=json.loads(value.pop('metadata_json'));value['excluded']=bool(value['excluded'])
        result.append(value)
    return result


def list_registry(db,include_excluded=True):
    init_registry(db)
    return {'items':[item for item in _rows(db) if include_excluded or not item['excluded']],
            'version':db.execute('SELECT COALESCE(MAX(version),0) FROM dynamic_topic_registry_history').fetchone()[0]}


def _write(db,item,action,previous=None):
    keys=('id','kind','label','terms','description','origin','excluded','status','metadata')
    if previous and all(previous.get(key)==item.get(key) for key in keys):return previous
    stamp=datetime.now(timezone.utc).isoformat()
    value=dict(item,created_at=(previous or {}).get('created_at',stamp),updated_at=stamp)
    version=db.execute('INSERT INTO dynamic_topic_registry_history(item_id,action,payload_json,created_at) VALUES (?,?,?,?)',
                       (item['id'],action,'{}',stamp)).lastrowid
    value['version']=version
    db.execute('UPDATE dynamic_topic_registry_history SET payload_json=? WHERE version=?',(json.dumps(value,ensure_ascii=False,sort_keys=True),version))
    db.execute('''INSERT INTO dynamic_topic_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,label=excluded.label,terms_json=excluded.terms_json,
        description=excluded.description,origin=excluded.origin,excluded=excluded.excluded,status=excluded.status,
        metadata_json=excluded.metadata_json,updated_at=excluded.updated_at,version=excluded.version''',
        (value['id'],value['kind'],value['label'],json.dumps(value['terms'],ensure_ascii=False),value['description'],
         value['origin'],int(value['excluded']),value['status'],_json(value['metadata']),value['created_at'],stamp,version))
    return value


def save_manual(db,payload):
    validated=_validate(payload)
    if 'excluded' in payload and not isinstance(payload['excluded'],bool):raise ValueError('excluded는 boolean이어야 합니다.')
    init_registry(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=next((item for item in _rows(db) if item['id']==payload.get('id')),None)
        if payload.get('id') and previous is None:raise ValueError('수정할 항목을 찾을 수 없습니다.')
        identity=previous['id'] if previous else 'manual:'+uuid.uuid4().hex
        if previous and identity.startswith('cooccurrence:'):
            validated['metadata']=dict(previous.get('metadata') or {},**validated['metadata'],match_mode='all',candidate_type='cooccurrence')
            if validated['terms']!=previous['terms']:
                validated['metadata']['keyword_ids']=[]
        return _write(db,dict(validated,id=identity,origin='manual',
            excluded=payload.get('excluded',(previous or {}).get('excluded',False)),status='active'),'manual_save',previous)


def set_excluded(db,id,excluded):
    if not isinstance(excluded,bool):raise ValueError('excluded는 boolean이어야 합니다.')
    init_registry(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        previous=next((item for item in _rows(db) if item['id']==id),None)
        return _write(db,dict(previous,excluded=excluded),'exclude' if excluded else 'restore',previous) if previous else None


def sync_automatic(db,candidates,*,namespace="dynamic"):
    if namespace not in ("dynamic","builtin","cooccurrence"):raise ValueError("지원하지 않는 자동 등록 범위입니다.")
    if not isinstance(candidates,list) or len(candidates)>500:raise ValueError('자동 후보는 최대 500개입니다.')
    normalized={}
    for candidate in candidates:
        value=_validate(candidate)
        identity=candidate.get('id') or ('dynamic-signal:' if value['kind']=='signal' else 'dynamic:')+hashlib.sha256(value['label'].casefold().encode()).hexdigest()[:24]
        builtin=candidate.get('origin')=='builtin'
        pattern=r'[A-Za-z0-9_:-]{1,100}' if builtin else (r'cooccurrence:[A-Za-z0-9_-]{1,80}' if namespace=='cooccurrence' else r'dynamic(?:-signal)?:[A-Za-z0-9_-]{1,80}')
        if not isinstance(identity,str) or not re.fullmatch(pattern,identity):
            raise ValueError('자동 항목 ID가 올바르지 않습니다.')
        if not builtin and identity.startswith('dynamic-signal:') != (value['kind']=='signal'):raise ValueError('ID와 항목 종류가 일치하지 않습니다.')
        # Preserve rule-derived display metrics without asserting model verification.
        extra={key:val for key,val in candidate.items() if key not in {'id','kind','label','name','terms','description','metadata','origin','excluded','status','version'}}
        value['metadata']=dict(extra,**value['metadata']);_json(value['metadata'])
        value['origin']='builtin' if builtin else 'auto'
        if identity in normalized and normalized[identity]!=value:raise ValueError('동일 ID의 자동 후보가 충돌합니다.')
        normalized[identity]=value
    init_registry(db)
    # Most view refreshes rediscover an identical catalog. A read-only no-op
    # must not contend with analysis writers; real changes still re-read under
    # BEGIN IMMEDIATE below, preserving manual edits and exclusions.
    existing=_rows(db)
    old={item['id']:item for item in existing}
    unchanged=True
    for identity,value in normalized.items():
        previous=old.get(identity)
        if previous and previous['origin']=='manual':continue
        if not previous or previous['status']!='active' or any(previous.get(k)!=v for k,v in value.items()):
            unchanged=False;break
    if unchanged:
        for identity,previous in old.items():
            owned=(namespace=='dynamic' and identity.startswith(('dynamic:','dynamic-signal:'))) or (namespace=='cooccurrence' and identity.startswith('cooccurrence:'))
            if owned and previous['origin']=='auto' and identity not in normalized and previous['status']!='dormant':
                unchanged=False;break
    if unchanged:return existing
    with db:
        db.execute('BEGIN IMMEDIATE')
        old={item['id']:item for item in _rows(db)}
        for identity,value in normalized.items():
            previous=old.get(identity)
            if previous and previous['origin']=='manual':continue
            _write(db,dict(value,id=identity,excluded=(previous or {}).get('excluded',False),status='active'),'automatic_sync',previous)
        for identity,previous in old.items():
            owned = (namespace=='dynamic' and identity.startswith(('dynamic:','dynamic-signal:'))) or (namespace=='cooccurrence' and identity.startswith('cooccurrence:'))
            if owned and previous['origin']=='auto' and identity not in normalized:
                _write(db,dict(previous,status='dormant'),'automatic_missing',previous)
    return _rows(db)
