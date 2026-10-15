import pytest
from graph_plans import simple_plan,prepare


@pytest.mark.parametrize('question',['최근 Physical AI 관련 뉴스 알려주세요','GPU 뉴스','최근 소버린 AI 뉴스 현황?'])
def test_single_topic_news_plan_needs_no_model(question,tmp_path,monkeypatch):
    monkeypatch.setattr('semantic.run_structured',lambda *a,**k:pytest.fail('unnecessary model call'))
    plan,cached=prepare(question,tmp_path/'news')
    assert not cached and plan['axes']==[question]
    assert prepare(question,tmp_path/'news')==(plan,True)


@pytest.mark.parametrize('question',[
    'Physical AI 벤치마크 방법 연구 현황과 데이터세트 종류, 이슈는?',
    'A와 B의 뉴스 비교','정책 변화의 영향과 위험은?',
    '최근 GPU와 TPU 관련 뉴스','최근 AI 뉴스가 산업에 미치는 영향은?',
])
def test_compound_questions_keep_full_planner(question):
    assert simple_plan(question) is None
