import json
import sqlite3
import subprocess
from pathlib import Path
import pytest
from search_query import parse, matches

CASES = [
    ('AI 반도체', ['ＡＩ 공급', '반도체'], True),
    ('AI AND 반도체 OR 로봇 -중국', ['로봇', '한국'], True),
    ('AI AND 반도체 OR 로봇 -중국', ['로봇', '중국'], False),
    ('"수출 통제"', ['수출', '통제'], False),
    ('"수출 통제"', ['수출 통제 강화'], True),
    ('-중국', ['한국'], True), ('C++ | [AI]', ['[AI]'], True),
    ('"OR"', ['OR'], True), ('', [''], True),
    ('"a\\"b"', ['a"b'], True),
]

@pytest.mark.parametrize('query,labels,expected', CASES)
def test_shared_semantics(query, labels, expected):
    assert matches(parse(query), labels) is expected


def test_python_javascript_parity():
    queries = [q for q, _, _ in CASES] + ['AI OR', 'OR AI', '"열림', '-', 'AI AND OR 반도체', 'a '*21, 'x'*257]
    script = "require('./static/observatory-search.js');console.log(JSON.stringify(JSON.parse(process.argv[1]).map(q=>ObservatorySearch.parse(q))));"
    results = json.loads(subprocess.check_output(['node', '-e', script, json.dumps(queries)], text=True))
    for query, js in zip(queries, results):
        if js['error']:
            with pytest.raises(ValueError): parse(query)
        else: assert parse(query) == js['groups']


def test_search_full_body_and_verified_analysis(tmp_path, monkeypatch):
    from app import connect, process_updates, read_news, read_links
    from source_store import init, save
    from news_search import filter_items
    from public_site import content, identity
    from test_completion_quality import fixture
    from completion_quality import message_text
    run, item = fixture()
    item.update(text='', day='2026-09-12', topic='general')
    with connect(tmp_path/'search.db') as db:
        # Test the real publication admission against a minimal ledger, not mocked acceptance.
        db.executescript('''CREATE TABLE corpus_completion_documents(document_id TEXT,status TEXT,workflow_run_id TEXT);
          CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT,updated_at TEXT);
          CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT);''')
        db.execute('INSERT INTO corpus_completion_documents VALUES (?, ?, ?)', (item['source_url'], 'complete', 'sample'))
        db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?)', ('sample','complete','','2026-09-12'))
        db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)', ('sample','final',json.dumps(run['results'])))
        monkeypatch.setattr('improvement_selection.all_corpus_items', lambda *a, **k: [item])
        found = filter_items(db, [item], '"실제 중단"')
        assert found and found[0]['search_matches'][0]['field'] == '심층 분석'
        assert not filter_items(db, [dict(item,title='다른 기사')], '"실제 중단"')
        item['source_context']['text'] = '바뀐 원문'
        assert not filter_items(db, [item], '"실제 중단"')
        item['source_context']['text'] = '운영자는 평가 후 재개 여부를 결정한다고 밝혔다.'
        run['results']['report']['claims'][0]['detail'] = '해시 불일치 변조'
        db.execute('UPDATE strategic_workflow_artifacts SET payload_json=?',(json.dumps(run['results']),))
        assert not filter_items(db, [item], '"실제 중단"')
        init(db)
        save(db,item['source_url'],dict(status='fetched',text='앞부분 '*1200+'깊은본문키워드',title='원문'), '2026-09-12T00:00:00+00:00')
        found = filter_items(db,[item], 'AI AND 깊은본문키워드')
        assert found and any('깊은본문키워드' in h['text'] for h in found[0]['search_matches'])
        assert not filter_items(db,[item], 'AI -깊은본문키워드')
        assert 'source_text' not in content(db,selected_items=[item])['news'][0]
        assert content(db,selected_items=[item],include_excerpts=True)['news'][0]['source_text'] == item['source_context']['text']


def test_news_links_and_strategy_share_full_scope_and_counts(tmp_path):
    from app import connect, process_updates, read_news, read_links
    from source_store import init, save
    from strategy_views import selected, card
    from unittest.mock import patch
    with connect(tmp_path/'api.db') as db:
        for index, title in enumerate(['AI 첫 기사', '로봇 두번째 기사'], 1):
            process_updates(db,[{'update_id':index,'channel_post':{
                'chat':{'id':-100123,'type':'channel','title':'채널'},'message_id':index,
                'date':1789228800,'text':title+'\nhttps://example.org/'+str(index)}}],{'-100123'})
        init(db)
        save(db,'https://example.org/1',dict(status='fetched',title='원문 제목',text='x'*4000+' 정밀진단'), '2026-09-12T00:00:00+00:00')
        params={'date':['all'],'q':['AI AND 정밀진단 OR 없는단어'],'page_size':['1'],'compact':['1']}
        news=read_news(db,params)
        assert news['total']==1 and len(news['items'])==1
        assert any(h['field']=='원문' and '정밀진단' in h['text'] for h in news['items'][0]['search_matches'])
        links=read_links(db,params)
        assert links['total']==1 and links['groups'][0]['search_matches']
        rows=read_news(db,{'date':['all']})['items']
        with patch('strategy.select_strategy_items',side_effect=lambda db, params, items, **kw:items):
            strategy=selected(db,params,{'items':rows,'morph':{}})
        assert len(strategy)==1 and card(strategy[0])['search_matches']
        assert read_news(db,dict(params,q=['AI -정밀진단']))['total']==0
        assert read_news(db,dict(params,q=['AI OR 로봇']))['total']==2
        assert read_news(db,dict(params,channel=['other']))['total']==0
        with pytest.raises(ValueError,match='키워드'):read_links(db,dict(params,q=['AI OR']))
