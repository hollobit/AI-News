import json
import sqlite3
import app
from improvement_selection import all_corpus_items
from risk_source_state import evidence_index_key,evidence_source_states,_snapshot_text


def test_url_id_reuse_does_not_mix_changed_and_current_text():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT,updated_at TEXT)')
    db.execute('INSERT INTO source_excerpts VALUES(?,?,?)',('https://example.org/a',json.dumps({'status':'fetched','title':'원문','text':'new','fetched_at':'2026-09-12'}),'2026-09-12'))
    old={'id':'url_shared','url':'https://example.org/a','origin':'fetched_url_excerpt','title':'원문','text':'old'}
    new=dict(old,text='new')
    states=evidence_source_states(db,[old,new])
    assert states[evidence_index_key(old)]['status']=='changed'
    assert states[evidence_index_key(new)]['status']=='matched_current'
    assert states[evidence_index_key(new)]['source_dates']==[]
    db.execute('UPDATE source_excerpts SET result_json=?',(json.dumps({'status':'failed','text':'new','title':'원문'}),))
    assert evidence_source_states(db,[new])[evidence_index_key(new)]['status']=='unavailable'
    db.close()


def test_current_telegram_source_date_edit_and_cache_invalidation(tmp_path):
    db=app.connect(tmp_path/'risk.sqlite')
    message={'chat':{'id':-1001,'type':'channel','title':'test'},'message_id':1,'date':1789167600,
             'text':'- [2026-07-16] AI 위험 연구 — 인공지능 통제를 분석했다.\nhttps://example.org/a'}
    app.process_updates(db,[{'update_id':1,'channel_post':message}],{'-1001'})
    item=next(i for i in all_corpus_items(db) if i.get('source_url')=='https://example.org/a')
    evidence={'id':'news_1','origin':'telegram_excerpt','url':item['source_url'],'text':_snapshot_text(item),'title':item['title'][:300],
              'channel':item['channel'],'message_id':str(item['message_id']),'published_at':'2026-09-12'}
    key=evidence_index_key(evidence)
    state=evidence_source_states(db,[evidence])[key]
    assert state['status']=='matched_current'
    assert state['source_dates']==['2026-07-16'] and state['date_basis']=='article_label'
    changed=dict(message,edit_date=message['date']+100,text=message['text'].replace('분석했다','철회했다'))
    app.process_updates(db,[{'update_id':2,'edited_channel_post':changed}],{'-1001'})
    assert evidence_source_states(db,[evidence])[key]['status']=='changed'
    db.close()


def test_posting_date_is_not_an_article_date(tmp_path):
    db=app.connect(tmp_path/'undated.sqlite')
    message={'chat':{'id':-1001,'type':'channel','title':'test'},'message_id':1,'date':1789167600,
             'text':'AI 위험 연구 — 인공지능 통제를 분석했다.\nhttps://example.org/a'}
    app.process_updates(db,[{'update_id':1,'channel_post':message}],{'-1001'})
    item=next(i for i in all_corpus_items(db) if i.get('source_url')=='https://example.org/a')
    evidence={'id':'news_1','origin':'telegram_excerpt','url':item['source_url'],'text':_snapshot_text(item),'title':item['title'][:300]}
    state=evidence_source_states(db,[evidence])[evidence_index_key(evidence)]
    assert state['status']=='matched_current'
    assert state['date_status']=='unknown' and state['source_dates']==[]
    db.close()
