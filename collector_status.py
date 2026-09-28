"""Bot update checks and actual received-message timestamps, never history claims."""
import json

PREFIX='collector_channel:'

def mark_checked(db,channels,stamp):
    for identity in channels:
        row=db.execute('SELECT value FROM state WHERE key=?',(PREFIX+str(identity),)).fetchone()
        value=json.loads(row[0]) if row else {}
        value['last_checked_at']=stamp
        db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(PREFIX+str(identity),json.dumps(value,ensure_ascii=False)))

def mark_received(db,message,stamp,changed):
    identity=str(message['chat']['id'])
    row=db.execute('SELECT value FROM state WHERE key=?',(PREFIX+identity,)).fetchone()
    value=json.loads(row[0]) if row else {}
    value.update(name=message['chat'].get('title',identity),last_received_at=stamp,
                 last_received_message_id=message['message_id'])
    if changed:value['last_saved_at']=stamp
    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(PREFIX+identity,json.dumps(value,ensure_ascii=False)))

def channel_status(db,channels,config_path):
    try:names={str(c['id']):c.get('name',str(c['id'])) for c in json.loads(config_path.read_text())['channels']}
    except (OSError,ValueError,KeyError):names={}
    rows={str(r['chat_id']):dict(r) for r in db.execute('SELECT chat_id,MAX(channel) name,COUNT(*) messages,MAX(published_at) last_published_at FROM news GROUP BY chat_id')}
    result=[]
    for identity in sorted(channels):
        row=db.execute('SELECT value FROM state WHERE key=?',(PREFIX+identity,)).fetchone()
        observed=json.loads(row[0]) if row else {}
        stored=rows.get(identity,{})
        result.append({'id':identity,'name':names.get(identity) or observed.get('name') or stored.get('name') or identity,
            'messages':stored.get('messages',0),'last_published_at':stored.get('last_published_at'),
            'last_checked_at':observed.get('last_checked_at'),'last_received_at':observed.get('last_received_at'),
            'last_saved_at':observed.get('last_saved_at'),'last_extracted_at':observed.get('last_extracted_at'),
            'status':'received' if observed.get('last_received_at') else 'historical' if stored.get('messages') else 'waiting'})
    heartbeat=db.execute("SELECT value FROM state WHERE key='collector_last_success'").fetchone()
    snapshot=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()
    return {'pipeline':json.loads(snapshot[0]) if snapshot else None,'channels':result,'last_success':heartbeat[0] if heartbeat else None,
            'scope':'봇 업데이트 확인 시각과 실제 메시지 수신 시각입니다. 채널의 과거 전체 이력 확인을 의미하지 않습니다.'}


def finish_extraction(db, channels, checked_at):
    """Persist coverage only after messages and article indexing have committed."""
    from datetime import datetime, timezone
    from improvement_selection import all_corpus_items
    previous=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()
    previous=json.loads(previous[0]) if previous else None
    items=all_corpus_items(db)
    stamp=datetime.now(timezone.utc).isoformat()
    identities={item['corpus_identity'] for item in items}
    prior_ids=db.execute("SELECT value FROM state WHERE key='collector_corpus_identities'").fetchone()
    old_ids=set(json.loads(prior_ids[0])) if prior_ids else None
    total=len(items)
    value={'status':'complete','checked_at':checked_at,'extracted_at':stamp,
           'total_unique':total,'previous_total':previous.get('total_unique') if previous else None,
           'new_unique':len(identities-old_ids) if old_ids is not None else None,
           'removed_unique':len(old_ids-identities) if old_ids is not None else None,
           'scope':'정규 URL 및 URL 없는 본문 중복 제거'}
    with db:
        db.execute("INSERT OR REPLACE INTO state VALUES ('collector_corpus_identities',?)",(json.dumps(sorted(identities)),))
        db.execute("INSERT OR REPLACE INTO state VALUES ('collector_corpus_snapshot',?)",(json.dumps(value),))
        for identity in channels:
            row=db.execute('SELECT value FROM state WHERE key=?',(PREFIX+str(identity),)).fetchone()
            state=json.loads(row[0]) if row else {}
            state['last_extracted_at']=stamp
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',(PREFIX+str(identity),json.dumps(state)))
    return value
