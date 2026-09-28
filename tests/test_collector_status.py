import json
from pathlib import Path
from app import connect, process_updates
from collector_status import channel_status, mark_checked


def test_channel_read_is_receipt_not_publication(tmp_path):
    db = connect(tmp_path / 'news.sqlite3')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'channels':[{'id':'-100123','name':'hollobit_news'},{'id':'-100456','name':'new'}]}))
    message = {'chat':{'id':-100123,'type':'channel','title':'News'},'message_id':1,'date':1789531200,'text':'Latest news'}
    process_updates(db,[{'update_id':1,'channel_post':message}],{'-100123'})
    before = channel_status(db,{'-100123','-100456'},config)['channels'][0]
    assert before['last_received_at'] != before['last_published_at']
    assert before['last_saved_at'] == before['last_received_at']
    with db:
        mark_checked(db,{'-100123','-100456'},'2026-09-16T17:00:00+09:00')
    rows = channel_status(db,{'-100123','-100456'},config)['channels']
    assert rows[0]['name'] == 'hollobit_news'
    assert rows[0]['last_received_at'] == before['last_received_at']
    assert rows[0]['last_checked_at'] == '2026-09-16T17:00:00+09:00'
    assert rows[1]['status'] == 'waiting'
    assert rows[1]['last_received_at'] is None
    db.close()


def test_extraction_runs_after_saved_messages_and_deduplicates_urls(tmp_path):
    from collector_status import finish_extraction
    db=connect(tmp_path/'news.sqlite3')
    first={'chat':{'id':-100123,'type':'channel','title':'News'},'message_id':1,'date':1789531200,
           'text':'AI 연구 소식\nhttps://example.org/research'}
    assert process_updates(db,[{'update_id':1,'channel_post':first}],{'-100123'})
    initial=finish_extraction(db,{'-100123'},'2026-09-16T17:00:00+09:00')
    assert initial['status']=='complete' and initial['total_unique']>0
    duplicate=dict(first,message_id=2,text='같은 연구 재게시\nhttps://example.org/research')
    process_updates(db,[{'update_id':2,'channel_post':duplicate}],{'-100123'})
    repeated=finish_extraction(db,{'-100123'},'2026-09-16T17:01:00+09:00')
    assert repeated['total_unique']==initial['total_unique'] and repeated['new_unique']==0
    new=dict(first,message_id=3,text='AI 신규 연구\nhttps://example.org/new')
    process_updates(db,[{'update_id':3,'channel_post':new}],{'-100123'})
    # A crash after the offset commit must leave extraction pending for restart.
    pending=json.loads(db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()[0])
    assert pending['status']=='extracting'
    assert db.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0]=='4'
    final=finish_extraction(db,{'-100123'},'2026-09-16T17:02:00+09:00')
    assert final['total_unique']==initial['total_unique']+1 and final['new_unique']==1
    db.close()
