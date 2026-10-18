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


def test_collection_wakes_analysis_before_keyword_preparation_without_writer_lock(tmp_path,monkeypatch):
    import sqlite3
    import app
    from keyword_index import _is_current,_source_revision
    path=tmp_path/'news.sqlite3';db=connect(path)
    events=[]
    message={'chat':{'id':-100123,'type':'channel'},'message_id':1,'date':1789531200,
             'text':'AI 연구 소식\nhttps://example.org/research?a=1&b=2'}
    def notify(actual_path):
        assert actual_path==path
        with sqlite3.connect(path) as reader:
            assert reader.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0]=='8'
            assert reader.execute('SELECT COUNT(*) FROM articles').fetchone()[0]>0
            snapshot=json.loads(reader.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()[0])
            assert snapshot['status']=='complete'
        assert not _is_current(db,_source_revision(db))
        events.append('wake')
    prepare=app.ensure_keyword_index
    def keywords(connection,factory):
        assert not connection.in_transaction
        with sqlite3.connect(path,timeout=0) as writer:
            writer.execute("INSERT INTO state VALUES ('concurrent_analysis','allowed')")
        events.append('keywords')
        return prepare(connection,factory)
    monkeypatch.setattr('pipeline_wakeup.notify',notify)
    monkeypatch.setattr(app,'ensure_keyword_index',keywords)
    app.process_collection_batch(db,[{'update_id':7,'channel_post':message}],{'-100123'},'checked',path)
    assert events==['wake','keywords']
    assert _is_current(db,_source_revision(db))
    status=channel_status(db,{'-100123'},tmp_path/'config.json')
    assert status['last_batch_metrics']['keywords_status']=='complete'
    db.close()


def test_failed_keyword_preparation_recovers_on_empty_poll_without_reingesting(tmp_path,monkeypatch):
    import sqlite3
    import pytest
    import app
    from keyword_index import _is_current,_source_revision
    path=tmp_path/'news.sqlite3';db=connect(path)
    message={'chat':{'id':-100123,'type':'channel'},'message_id':1,'date':1789531200,
             'text':'AI 뉴스\nhttps://example.org/research'}
    wakes=[];monkeypatch.setattr('pipeline_wakeup.notify',lambda _:wakes.append(1))
    prepare=app.ensure_keyword_index
    def fail(*_):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(app,'ensure_keyword_index',fail)
    with pytest.raises(sqlite3.OperationalError):
        app.process_collection_batch(db,[{'update_id':7,'channel_post':message}],{'-100123'},'before',path)
    assert db.execute("SELECT value FROM state WHERE key='offset'").fetchone()[0]=='8'
    assert not _is_current(db,_source_revision(db))
    assert channel_status(db,{'-100123'},tmp_path/'missing')['last_batch_metrics']['keywords_status']=='failed'
    monkeypatch.setattr(app,'ensure_keyword_index',prepare)
    app.process_collection_batch(db,[],{'-100123'},'after',path)
    assert _is_current(db,_source_revision(db))
    assert db.execute('SELECT COUNT(*) FROM news').fetchone()[0]==1
    assert wakes==[1]
    metrics=channel_status(db,{'-100123'},tmp_path/'missing')['last_batch_metrics']
    assert metrics['keywords_status']=='complete' and metrics['keywords_recovered']
    assert 'keyword_error' not in metrics
    db.close()


def test_identity_count_matches_full_corpus_and_uses_no_body_decoding(tmp_path,monkeypatch):
    import source_projection
    from improvement_selection import all_corpus_items
    from collector_status import finish_extraction
    path=tmp_path/'news.sqlite3';db=connect(path)
    for mid,text in [(1,'AI 뉴스\nhttps://example.org/a'),(2,'AI 추가 관측\nhttps://example.org/a'),(3,'본문만 있는 별도 소식')]:
        message={'chat':{'id':-100123,'type':'channel'},'message_id':mid,'date':1789531200,'text':text}
        process_updates(db,[{'update_id':mid,'channel_post':message}],{'-100123'})
    expected={r['corpus_identity'] for r in all_corpus_items(db)}
    assert source_projection.corpus_identities(db)==expected
    def unexpected(*_):raise AssertionError('count must not load full article bodies')
    with monkeypatch.context() as m:
        m.setattr('improvement_selection.all_corpus_items',unexpected)
        result=finish_extraction(db,{'-100123'},'now')
    assert result['total_unique']==len(expected)
    # If a projection is unavailable, keep the existing full validation path.
    monkeypatch.setattr(source_projection,'corpus_identities',lambda _:None)
    fallback=finish_extraction(db,{'-100123'},'later')
    assert fallback['total_unique']==len(expected) and fallback['new_unique']==0
    db.close()


def test_http_news_does_not_publish_stale_keyword_membership(tmp_path):
    import sqlite3
    import pytest
    from app import read_news
    from keyword_index import ensure_keyword_index
    from news_repository import joined_articles
    from projection_cache import read_projections
    db=connect(tmp_path/'news.sqlite3')
    message={'chat':{'id':-100123,'type':'channel'},'message_id':1,'date':1789531200,
             'text':'새로운 연구\nhttps://example.org/research'}
    process_updates(db,[{'update_id':1,'channel_post':message}],{'-100123'},prepare_keywords=False)
    before=db.total_changes
    with read_projections(),pytest.raises(sqlite3.OperationalError,match='preparation pending'):
        read_news(db,{'date':['all']})
    assert db.total_changes==before and not db.in_transaction
    ensure_keyword_index(db,lambda:joined_articles(db))
    with read_projections():
        assert read_news(db,{'date':['all']})['items']
    db.close()


def test_news_detects_edit_during_keyword_selection(tmp_path,monkeypatch):
    import sqlite3
    import pytest
    import news_views
    from keyword_index import mark_keyword_source_changed
    from projection_cache import read_projections
    path=tmp_path/'news.sqlite3';db=connect(path)
    message={'chat':{'id':-100123,'type':'channel'},'message_id':1,'date':1789531200,
             'text':'연구 기사\nhttps://example.org/research'}
    process_updates(db,[{'update_id':1,'channel_post':message}],{'-100123'})
    original=news_views._keyword_membership
    def changed(connection,items):
        with sqlite3.connect(path) as writer:mark_keyword_source_changed(writer)
        return original(connection,items)
    monkeypatch.setattr(news_views,'_keyword_membership',changed)
    with read_projections(),pytest.raises(sqlite3.OperationalError,match='changed during'):
        news_views.read_news(db,{'date':['all']})
    db.close()
