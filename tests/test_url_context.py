from bulk_baseline import freeze_item
from url_context import focus_url_context


def test_late_briefing_url_keeps_its_own_topic_inside_baseline_budget():
    earlier = '\n\n'.join(f'이전 기사{i} 다른 회사의 소식\nhttps://example.com/old{i}' for i in range(45))
    target = '**대상 로봇 연구**\n목표 기사의 실험 결과와 한계입니다.\n🔗 https://example.com/target'
    text = earlier+'\n\n'+target+'\n\n다음 뉴스: 의료 정책\nhttps://example.com/next'
    original = {'title': '오늘 종합 브리핑', 'text': text, 'source_url':'https://example.com/old0'}
    result = focus_url_context(original, 'https://example.com/target')
    assert '대상 로봇 연구' in result['title']
    assert '목표 기사의 실험' in result['text']
    assert '이전 기사' not in result['text'] and '다음 뉴스' not in result['text']
    assert original['text'] == text
    frozen = freeze_item(result)
    assert '목표 기사의 실험' in frozen['evidence'][0]['text']
    scope=result['url_context']
    assert text[scope['start']:scope['end']]==result['text']


def test_link_only_paragraph_attaches_only_immediately_preceding_story():
    text='다른 기사\nhttps://example.com/other\n\n대상 제목\n대상의 상세 설명\n\nhttps://example.com/target\n\n새로운 후속 기사'
    result=focus_url_context({'text':text},'https://example.com/target')
    assert result['text']=='대상 제목\n대상의 상세 설명\n\nhttps://example.com/target'
    assert '다른 기사' not in result['text'] and '후속 기사' not in result['text']


def test_unseparated_link_lines_do_not_borrow_previous_article():
    text='이전 제목\n이전 설명\nhttps://example.com/other\n대상 제목\n대상 설명\nhttps://example.com/target\n다음 제목\nhttps://example.com/next'
    result=focus_url_context({'text':text},'https://example.com/target')
    assert result['text']=='대상 제목\n대상 설명\nhttps://example.com/target'
    assert result['url_context']['verbatim']


def test_shared_link_line_keeps_only_explicit_target_label():
    text='[이전 업체](https://example.com/other) 및 [대상 연구](https://example.com/target)'
    result=focus_url_context({'text':text},'https://example.com/target')
    assert result['text']=='대상 연구\nhttps://example.com/target'
    assert not result['url_context']['verbatim']


def test_exact_single_article_is_not_rewritten():
    item={'title':'하나의 기사','text':'하나의 기사\n본문\nhttps://example.com/a','source_url':'https://example.com/a'}
    result=focus_url_context(item,'https://example.com/a?utm_source=x')
    assert result['title']==item['title'] and result['text']==item['text']
    assert result['url_context']['method']=='single_article'


def test_focused_article_date_does_not_inherit_briefing_or_posting_date():
    text='AI 뉴스 브리핑 | 2026-07-21\n\n**[2026-07-20] AI 전력망 토지 수용**\n재산권 문제를 설명한다.\nhttps://example.com/power\n\n**[2026-07-20] AI 웹 가독성 도구**\n별도 도구 소개.\nhttps://example.com/tool'
    item={'title':'전체 브리핑','text':text,'day':'2026-07-21','date_basis':'briefing','source_url':'https://example.com/tool'}
    tool=focus_url_context(item,'https://example.com/tool')
    power=focus_url_context(item,'https://example.com/power')
    assert '토지 수용' not in tool['text'] and '재산권' not in tool['text']
    assert '가독성' not in power['text']
    assert power['day']=='2026-07-20' and power['date_basis']=='article'
    assert power['original_day']=='2026-07-21'
    assert power['url_context']['article_date_quote']=='**[2026-07-20] AI 전력망 토지 수용**'
    changed=focus_url_context(dict(item,text=text.replace('[2026-07-20] AI 전력망','[2026-07-19] AI 전력망')),'https://example.com/power')
    assert changed['day']=='2026-07-19' and changed['url_context']['original_text_hash']!=power['url_context']['original_text_hash']


def test_source_specific_journal_date_and_unrelated_pubmed_story_stay_separate():
    text='■ **npj Digital Medicine | 2026-08-20**\n병원 간 EHR 모델 성능 저하\nhttps://nature.com/articles/a\n\n**법의학 ML 연구**\n대퇴골로 성별과 연령 추정\nhttps://pubmed.ncbi.nlm.nih.gov/42622651/'
    item={'text':text,'day':'2026-08-21','date_basis':'briefing'}
    nature=focus_url_context(item,'https://nature.com/articles/a')
    pubmed=focus_url_context(item,'https://pubmed.ncbi.nlm.nih.gov/42622651')
    assert nature['day']=='2026-08-20' and nature['date_basis']=='article'
    assert '병원 간' not in pubmed['text'] and '대퇴골' in pubmed['text']
    assert pubmed['date_basis']=='briefing'


def test_article_month_day_heading_uses_explicit_briefing_year_context():
    text='5️⃣ **EHR 모델의 병원간 성능 저하** (08-20)\n실험 설명\nhttps://nature.com/articles/a\n\n다른 법의학 연구\nhttps://pubmed.ncbi.nlm.nih.gov/1/'
    result=focus_url_context({'text':text,'day':'2026-08-21','date_basis':'briefing'},'https://nature.com/articles/a')
    assert result['day']=='2026-08-20' and result['date_basis']=='article'
