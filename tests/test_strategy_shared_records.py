import sqlite3
from unittest.mock import patch
from strategy import select_strategy_items
from strategy_trends import trend_metrics, filter_strategic_keyword
from morphology import keyword_records
from strategic_value import evaluate_news
from sector_taxonomy import classify_sectors


def test_shared_records_precomputed_matches_default():
    db=sqlite3.connect(':memory:')
    items=[{'chat_id':1,'message_id':i,'item_index':0,'title':'정부 소버린 AI 교육 투자',
            'text':'정부가 국가경제와 교육을 위한 소버린 AI 투자를 발표했다.',
            'day':'2026-09-'+str(10+i),'source_url':'https://example.org/'+str(i)} for i in range(4)]
    records=keyword_records(db,items)
    wanted=next(term['id'] for term in next(iter(records.values())) if term['label']=='소버린 AI')
    params={'strategic_keyword':[wanted],'sort':['strategic']}
    expected=select_strategy_items(db,params,items)
    expected_trends=trend_metrics(db,items)
    prepared=[dict(item,strategic_value=evaluate_news(item),sectors=classify_sectors(item)) for item in items]
    with (patch('morphology.keyword_records',side_effect=AssertionError('recomputed morphology')),
         patch('strategic_value.evaluate_news',side_effect=AssertionError('recomputed value')),
         patch('strategy_trends.evaluate_news',side_effect=AssertionError('recomputed trend value')),
         patch('sector_taxonomy.classify_sectors',side_effect=AssertionError('recomputed sectors'))):
        actual=select_strategy_items(db,params,prepared,records=records,precomputed=True)
        trends=trend_metrics(db,prepared,records=records,precomputed=True)
    assert actual==expected
    assert trends==expected_trends


def test_explicit_empty_records_never_reloads():
    with patch('morphology.keyword_records',side_effect=AssertionError('reloaded')):
        assert filter_strategic_keyword(None,[{'title':'test'}],{'strategic_keyword':['missing']},records={})==[]
        assert trend_metrics(None,[],records={})['emerging']==[]
