from copy import deepcopy

from graph_answer_format import generation_evidence
from graph_question_context import focus


def test_small_bundle_keeps_quantities_and_conditions_outside_ranked_windows():
    question='벤치마크 데이터세트 규모와 평가 방법의 한계'
    evidence=[dict(id='source',title='Benchmark',source_kind='paper',
                   text='Dataset benchmark evaluation methods. '*35+
                   'The dataset includes 139 scenes and 282.5 hours of synchronized video. '
                   'Only two warehouses were evaluated. '
                   +'Background context. '*120)]
    before=deepcopy(evidence)
    result=generation_evidence(evidence,question)
    assert len(evidence[0]['text'])>3600
    assert result==before and result[0] is not evidence[0]
    result[0]['text']='caller edit'
    assert evidence==before


def test_small_bundle_does_not_drop_uncited_or_contradictory_sources():
    sources=[dict(id='first',text='A reported result. '*260),
             dict(id='counter',text='The result did not replicate. '*150)]
    assert generation_evidence(sources,'reported result')==sources
    assert generation_evidence([], 'question')==[]


def test_large_bundle_keeps_existing_windows_and_review_originals():
    sources=[dict(id=str(i),text='Dataset benchmark evaluation. '*150+
                  'However, performance outdoors was not established. ') for i in range(4)]
    before=deepcopy(sources)
    result=generation_evidence(sources,'dataset benchmark performance')
    assert sum(len(e['text']) for e in sources)>12000
    assert result==[focus(e,'dataset benchmark performance') for e in sources]
    assert all(e['source_text_hash'] and e['excerpt_segments'] for e in result)
    assert sources==before
