import copy
from graph_rag import GraphResult,answer_question,validate_answer


def test_detailed_relational_answer_preserves_review_and_separate_cache(monkeypatch):
    evidence=[dict(id='e',title='Physical AI 벤치마크',text='제공된 실험 방법과 한계',source_url='https://example.org',day='2026-10-10')]
    graph=GraphResult(nodes=[],edges=[],evidence=evidence);graph.search_key='context-question-test'
    retrieval=dict(nodes=[],edges=[],evidence=evidence,no_hits=False,limitations=[])
    calls=[]
    def runner(prompt,schema,**kwargs):
        calls.append((prompt,schema,kwargs))
        if 'checks' in schema['properties']:
            value=dict(checks=[dict(claim_index=0,supported=True,reason='원문 대조')],answer_supported=True,missing_information=[],citation_corrections=[])
            if 'quality' in schema['properties']:
                from test_answer_quality import audit
                count=schema['properties']['quality']['properties']['axes']['minItems']
                value['quality']=audit(axes=('addressed',)*count)['quality']
            return value
        item=schema['properties']['claims']['items']
        fields=item.get('anyOf',[item])[0]['properties']
        c=dict(text='실험 조건에 따른 비교 한계',evidence_ids=['e'])
        if 'premises' in fields:c.update(premises='원문 조건',assumptions='새 환경에 동일 적용한다는 가정',counterevidence='미확보',uncertainty='범위 제한')
        if 'kind' in fields:c.update(kind='connection',axis='방법과 데이터 의존성')
        if 'sentences' in fields or 'passage' in fields:
            from test_answer_quality import wire_claim
            c=wire_claim(c)
        value=dict(claims=[c],limitations=[0] if 'paragraphs' in schema['properties'] else ['조건부 해석'])
        value.update(paragraphs=[[0]]) if 'paragraphs' in schema['properties'] else value.update(answer='근거에 따른 종합')
        return value
    monkeypatch.setattr('semantic.run_structured',runner)
    result=answer_question(graph,'Physical AI 벤치마크',retrieval=retrieval,analysis_plan={'axes':['방법','데이터']},detailed=True)
    assert result['verification']['method']=='independent_evidence_review'
    assert result['claims'][0]['kind']=='connection'
    assert calls[0][1]['properties']['claims']['maxItems']==8
    assert calls[0][2]['role']=='graph_draft' and calls[0][2]['reasoning_effort']=='medium'
    assert calls[1][2]['role']=='graph_answer' and calls[1][2]['reasoning_effort']=='medium'
    assert '자료 사이' in calls[0][0] and '원문' in calls[1][0]
    answer_question(graph,'Physical AI 연구',retrieval=retrieval,analysis_plan={'axes':['방법','데이터']})
    assert len(calls)==4
    assert calls[2][1]['properties']['claims']['maxItems']==4


def test_detailed_review_rebuild_keeps_supported_insight_first(monkeypatch):
    from graph_rag import review_answer
    claims=[dict(text='관측 사실',kind='finding',evidence_ids=['e']),
            dict(text='조건부 통찰',kind='insight',evidence_ids=['e']),
            dict(text='근거 없는 연결',kind='connection',evidence_ids=['e'])]
    monkeypatch.setattr('semantic.run_structured',lambda *a,**kw:dict(
        checks=[dict(claim_index=i,supported=i<2,reason='검토') for i in range(3)],
        answer_supported=False,missing_information=['확인되지 않은 연결']))
    out=review_answer(dict(answer='원래 요약',claims=claims,limitations=[]),
        dict(question='관계는?',evidence=[dict(id='e',text='원문')],answer_profile='integrated-detailed-v1'))
    assert len(out['claims'])==2
    assert out['answer'].index('조건부 통찰')<out['answer'].index('관측 사실')
    assert '근거 없는 연결' not in out['answer']
    assert out['verification']['removed_unsupported_claims']==1
    assert '확인되지 않은 연결' in out['limitations']
