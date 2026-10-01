import json
import sqlite3
from unittest.mock import patch
from app import connect,process_updates
from source_enrichment import init_sources
from source_changes import install
from improvement_selection import all_corpus_items
from news_repository import joined_articles,unique_articles
from projection_cache import read_projections


def post(db,mid,text,day=0,entities=None):
    message={'chat':{'id':-100123,'type':'channel'},'message_id':mid,'date':1789531200+86400*day,'text':text}
    if entities:message['entities']=entities
    process_updates(db,[{'update_id':mid,'channel_post':message}],{'-100123'})
    db.commit()


def assert_equivalent(db):
    actual=all_corpus_items(db)
    rows=unique_articles(joined_articles(db))
    with patch('source_projection.corpus',return_value=None),patch('source_projection.joined',return_value=None):
        expected=all_corpus_items(db)
        expected_rows=unique_articles(joined_articles(db))
    assert actual==expected
    assert actual.coverage==expected.coverage
    assert rows==expected_rows
    return actual


def metrics(path):
    with sqlite3.connect(str(path)+'.source-projections.sqlite3') as db:
        return json.loads(db.execute('SELECT metrics FROM meta').fetchone()[0])


def test_changes_representatives_hidden_links_and_source_edits_match_full_rebuild(tmp_path):
    path=tmp_path/'news.db';db=connect(path)
    init_sources(db);install(db);db.commit()
    post(db,1,'의료 AI 진단 연구\nhttps://example.org/a')
    post(db,2,'의료 AI 진단 연구\nhttps://example.org/a',day=1) # duplicate message
    post(db,3,'공공 행정 AI\nhttps://example.org/b')
    post(db,4,'공공 AX 업데이트\nhttps://example.org/a',day=2) # latest URL representative
    post(db,5,'숨은 링크 기사',entities=[{'type':'text_link','offset':0,'length':2,'url':'https://example.org/hidden'}])
    post(db,6,'URL 없는 정책 변화')
    assert_equivalent(db)
    post(db,3,'공공 행정 AI 업데이트\nhttps://example.org/b')
    assert_equivalent(db)
    assert metrics(path)['changed_messages']==1
    db.execute("INSERT INTO source_excerpts VALUES(?,?,?)",('https://example.org/a',json.dumps({'status':'fetched','title':'원문','text':'새 원문 근거'}),'now'));db.commit()
    assert_equivalent(db)
    assert metrics(path)['changed_messages']==3
    # Remove the current URL representative; the original and its duplicate
    # still have to select the first message, not the latest repost.
    db.execute('DELETE FROM articles WHERE message_id=4');db.execute('DELETE FROM news WHERE message_id=4');db.execute('DELETE FROM archived_urls WHERE message_id=4');db.commit()
    assert_equivalent(db)
    db.execute('DELETE FROM articles WHERE message_id=1');db.execute('DELETE FROM news WHERE message_id=1');db.execute('DELETE FROM archived_urls WHERE message_id=1');db.commit()
    assert_equivalent(db)
    post(db,5,'숨은 링크 기사',entities=[{'type':'text_link','offset':0,'length':2,'url':'https://example.org/replaced'}])
    assert_equivalent(db)
    db.close()


def test_read_only_miss_does_not_advance_and_history_gap_rebuilds(tmp_path):
    path=tmp_path/'news.db';db=connect(path)
    post(db,1,'의료 AI\nhttps://example.org/a');assert_equivalent(db)
    old=metrics(path)
    post(db,2,'공공 AX\nhttps://example.org/b')
    with read_projections():assert_equivalent(db)
    assert metrics(path)==old
    db.execute('UPDATE source_change_state SET floor=(SELECT MAX(seq) FROM source_changes)');db.execute('DELETE FROM source_changes');db.commit()
    assert_equivalent(db)
    assert metrics(path)['mode']=='history_gap'
    db.close()


def test_single_change_reads_only_affected_messages_and_restart_reuses(tmp_path):
    path=tmp_path/'news.db';db=connect(path)
    for i in range(20):post(db,i,f'기사 {i}\nhttps://example.org/{i}')
    assert_equivalent(db);db.close()
    db=connect(path)
    with patch('source_projection._message',side_effect=AssertionError('unchanged message rebuilt')):
        all_corpus_items(db)
    post(db,3,'기존 기사 수정\nhttps://example.org/3')
    from source_projection import _message
    with patch('source_projection._message',wraps=_message) as build:
        assert_equivalent(db)
        assert build.call_count==1
    db.close()


def test_focused_url_keeps_scheduling_id_from_original_url(tmp_path):
    path=tmp_path/'news.db';db=connect(path)
    post(db,1,'의료 AI\nhttps://example.org/a')
    from url_context import focus_url_context
    def redirected(row,url):
        return dict(focus_url_context(row,url),source_url='https://example.org/canonical')
    with patch('url_context.focus_url_context',side_effect=redirected),patch('improvement_selection.focus_url_context',side_effect=redirected):
        assert_equivalent(db)
    db.close()


def test_date_pushdown_and_pagination_match_legacy(tmp_path):
    from news_repository import read_news
    db=connect(tmp_path/'news.db')
    for i in range(8):post(db,i,f'AI 기사 {i}\nhttps://example.org/{i}',day=i%2)
    actual=read_news(db,{},include_discovery=False)
    with patch('source_projection.news_dates',return_value=None),patch('source_projection.joined',return_value=None):
        expected=read_news(db,{},include_discovery=False)
    assert actual==expected
    paged=read_news(db,{'page_size':['2'],'page':['2']},include_discovery=False)
    assert paged['items']==actual['items'][2:4]
    assert paged['total']==len(actual['items'])
    selected=all_corpus_items(db,{'https://example.org/3'})
    assert [x['source_url'] for x in selected]==['https://example.org/3']
    db.close()


def test_compact_news_retains_provenance_and_loads_full_body_by_stable_id(tmp_path):
    from news_repository import read_news
    db=connect(tmp_path/'news.db')
    post(db,1,'공공 AI 설명\n긴 원문 설명입니다.\nhttps://example.org/a?key=1&part=2')
    full=read_news(db,{},include_discovery=False)
    compact=read_news(db,{'compact':['1'],'page_size':['40']},include_discovery=False)
    item=compact['items'][0]
    assert 'text' not in item and 'source_context' not in item
    assert item['detail_id']==full['items'][0]['detail_id']
    detail=read_news(db,{'date':[item['day']],'detail_id':[item['detail_id']]},include_discovery=False)
    assert detail['items']==full['items']
    assert read_news(db,{'detail_id':['a'*64]},include_discovery=False)['items']==[]
    db.close()
