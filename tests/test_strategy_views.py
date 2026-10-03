import json
from unittest.mock import patch
import pytest
import app
from source_enrichment import SourceService
from strategy_views import dataset,read_view,read_item


@pytest.fixture
def corpus(tmp_path):
    path=tmp_path/'view.sqlite'; db=app.connect(path)
    for i in range(1,5):
        message={'chat':{'id':-1001,'type':'channel','title':'test'},'message_id':i,'date':1789167600+i,
                 'text':f'- [2026-09-12] 전략 기사 {i} — 소버린 AI 교육 투자 {i}\nhttps://example.org/{i}'}
        app.process_updates(db,[{'update_id':i,'channel_post':message}],{'-1001'})
    yield db,path
    db.close()


def test_filter_pagination_detail_and_response_isolation(corpus):
    db,_=corpus
    first=read_view(db,{'page_size':['2'],'sort':['latest']},{})
    second=read_view(db,{'page':['2'],'page_size':['2'],'sort':['latest']},{})
    assert first['total']==4 and first['pagination']['has_more']
    assert not second['pagination']['has_more']
    assert {i['item_id'] for i in first['items']}.isdisjoint(i['item_id'] for i in second['items'])
    item_id=first['items'][0]['item_id']
    detail=read_item(db,item_id)['item']
    assert detail['text'] and detail['item_id']==item_id
    first['items'][0]['strategic_value']['score']=-999
    detail['strategic_keywords'].clear()
    assert read_item(db,item_id)['item']['strategic_value']['score']>=0
    assert read_item(db,item_id)['item']['strategic_keywords']
    assert read_item(db,'missing') is None
    assert read_view(db,{'q':['투자 2']},{})['total']==4  # implicit AND also matches the date's 2
    filtered=read_view(db,{'q':['"투자 2"']},{})
    assert filtered['total']==1 and '기사 2' in filtered['items'][0]['title']
    assert read_view(db,{'channel':['unknown']},{})['total']==0
    assert read_view(db,{'date':['2026-09-11']},{})['total']==0


def test_invalid_filter(corpus):
    db,_=corpus
    for params in ({'date':['bad']},{'keyword':['bad']},{'sector':['education-invalid']}):
        with pytest.raises(ValueError): read_view(db,params,{})


def test_source_change_invalidates_projection_and_failed_text_is_not_evidence(corpus):
    db,path=corpus
    before=dataset(db)
    response={'status':'fetched','title':'원문','text':'수출통제 export controls'}
    service=SourceService(path,fetcher=lambda _:response)
    try:
        service.fetch('https://example.org/1')
        after=dataset(db)
        assert after['revision']!=before['revision']
        item=next(i for i in after['items'] if i['source_url']=='https://example.org/1')
        assert any(t['label']=='수출통제' for t in item['strategic_keywords'])
        assert any(d['id']=='exports' for d in item['strategic_value']['domains'])
        response.update(status='failed',error='HTTP 403')
        service.fetch('https://example.org/1',True)
        failed=dataset(db)
        item=next(i for i in failed['items'] if i['source_url']=='https://example.org/1')
        assert not any(t['label']=='수출통제' for t in item['strategic_keywords'])
        assert not any(d['id']=='exports' for d in item['strategic_value']['domains'])
    finally: service.close()


def test_overview_uses_dataset_revision_and_none_bypasses(corpus):
    db,_=corpus
    old=dataset(db)
    old=dict(old,revision=None)
    calls=[]
    def cached(connection,namespace,revision,builder,**kwargs):
        calls.append(revision); return builder()
    with patch('strategy_views.dataset',return_value=old), patch('strategy_views.cached_read',side_effect=cached), patch('strategy_views.revision_token',side_effect=AssertionError('must use dataset revision')):
        result=read_view(db,{'view':['overview']},{})
    assert result['total']==4 and calls==[None]


def test_compact_nested_summary_and_historical_research_label(corpus):
    from strategy_views import card
    assert card({'base_analysis':{'status':'verified','result':{'summary':'검토 요약'}}})['base_analysis']['summary']=='검토 요약'
    assert card({'base_analysis':{'summary':'상위 요약','result':{'summary':'이전 요약'}}})['base_analysis']['summary']=='상위 요약'
    db,_=corpus
    db.execute('CREATE TABLE research_runs(id TEXT PRIMARY KEY,created_at TEXT)')
    db.execute('CREATE TABLE research_documents(canonical_url TEXT,status TEXT,analysis_json TEXT,run_id TEXT,doc_id TEXT,source_status TEXT)')
    db.execute("INSERT INTO research_runs VALUES('legacy','2026-09-12')")
    db.execute('INSERT INTO research_documents VALUES(?,?,?,?,?,?)',('https://example.org/1','complete',json.dumps({'summary':'기존 연구 요약'}),'legacy','doc','fetched'))
    db.commit()
    data=dataset(db)
    item=next(i for i in data['items'] if i['source_url']=='https://example.org/1')
    detail=read_item(db,item['item_id'])['item']
    assert detail['strategic_analysis']['freshness']=='historical_unchecked'
    assert card(detail)['strategic_analysis']['freshness']=='historical_unchecked'
