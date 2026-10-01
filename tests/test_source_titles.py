from source_titles import title_projection


def item():
    return dict(title='브리핑: 독파모 계속',source_url='https://example.org/news?id=1',
                source_context=dict(status='fetched',url='https://example.org/news?id=1',
                                    title='중단설 휩싸인 독파모',text='배경훈은 사업을 계속한다고 밝혔다.'))


def test_display_preserves_distinct_original_and_briefing_titles_without_mutation():
    original=item()
    display=title_projection(original)
    assert display['title']=='중단설 휩싸인 독파모'
    assert display['briefing_title']=='브리핑: 독파모 계속'
    assert display['title_origin']=='source_page'
    assert original['title']=='브리핑: 독파모 계속'


def test_failed_missing_foreign_or_challenge_sources_cannot_supply_headlines():
    for change in ({'status':'failed'}, {'url':'https://example.org/news?id=2'},
                   {'title':'Just a moment...'}, {'text':''}, {'error':'blocked'}):
        original=item();original['source_context'].update(change)
        display=title_projection(original)
        assert display['title_origin']=='telegram_briefing'
        assert display['title']==original['title']
        assert display['source_title']==''
