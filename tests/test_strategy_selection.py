from unittest.mock import patch
import pytest
import app
from strategy import select_strategy_items

def test_priority_and_domain_scope_shared_with_simulation():
    rows = [{'title':'AI release', 'day':'2026-09-15'},
            {'title':'미국 정부 수출통제와 반도체 공급망', 'day':'2026-09-14'},
            {'title':'학교 교육', 'day':'2026-09-13'}]
    with patch('news_repository.read_news', return_value={'items': rows}):
        selected = select_strategy_items(None, {'sort':['strategic']})
        assert selected[0]['title'] == rows[1]['title']
        assert select_strategy_items(None, {'sort':['latest']})[0]['title'] == rows[0]['title']
        scoped = app.simulation_news(None, {'filters': {'impact':'education','sort':'strategic'}, 'limit':1})
        assert [r['title'] for r in scoped] == ['학교 교육']
        assert scoped[0]['strategic_value']['domains'][0]['id'] == 'education'
        with pytest.raises(ValueError):
            select_strategy_items(None, {'impact':['invented']})
