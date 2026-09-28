import sqlite3
from unittest.mock import patch
from improvement_selection import all_corpus_items, content_identity, select_improvement_news


def test_full_corpus_includes_secondary_hidden_and_urlfree_without_reposts():
    rows = [dict(title='뉴스',text='내용 https://example.com/a https://example.com/b',source_url='https://example.com/a?utm_source=x',day='2026-09-15'),
            dict(title='뉴스',text='내용 https://example.com/a https://example.com/b',source_url='https://example.com/a',day='2026-09-14'),
            dict(title='교육',text='학교 교육',source_url='',day='2026-09-15'),
            dict(title='교육',text='학교   교육',source_url='',day='2026-09-14')]
    hidden = [dict(title='숨은 링크',text='숨은 링크',source_url='https://example.com/hidden',day='2026-09-15')]
    with patch('news_repository.joined_articles',return_value=rows), patch('news_repository.unindexed_link_rows',return_value=[]), patch('news_repository.hidden_link_rows',return_value=hidden), patch('source_enrichment.attach_sources',side_effect=lambda db,x:x):
        result=all_corpus_items(None)
    assert result.coverage['total_unique']==4
    assert result.coverage['duplicates_excluded']==3
    assert {r['source_url'] for r in result}=={'','https://example.com/a','https://example.com/b','https://example.com/hidden'}
    assert len({content_identity(r) for r in result})==4
    from keyword_index import keyword_record_id
    assert len({keyword_record_id(r) for r in result})==4


def test_seen_urls_are_deprioritized_not_lost_for_changed_source_check():
    rows=[dict(title='정부 국가안보',text='정부 국가안보',source_url='https://example.com/seen',day='2026-09-15'),
          dict(title='기타',text='기타',source_url='https://example.com/new',day='2026-09-14')]
    with patch('news_repository.joined_articles',return_value=rows), patch('news_repository.unindexed_link_rows',return_value=[]), patch('news_repository.hidden_link_rows',return_value=[]), patch('source_enrichment.attach_sources',side_effect=lambda db,x:x):
        result=select_improvement_news(None,{'full_corpus':True},[],['https://example.com/seen'])
    assert [r['source_url'] for r in result]==['https://example.com/new','https://example.com/seen']
    assert result.coverage['total_unique']==2
