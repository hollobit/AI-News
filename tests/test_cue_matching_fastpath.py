import pytest
from sector_taxonomy import _match, _pattern, SECTORS
from strategic_value import evaluate_news


@pytest.mark.parametrize('text',[
    '', 'enterprise\n\t ai and health\u2003care', 'ragged axolotl clinicals xgpu2',
    'ai 전환 의료 임상환자 transformer cybersecurity', 'gpu rag ax ai healthcare',
])
def test_absent_token_fastpath_preserves_regex_semantics(text):
    for sector in SECTORS:
        for term in sector['terms']:
            assert _match(text,term)==bool(_pattern(term).search(text))


def test_priority_preserves_whitespace_and_word_boundaries():
    result=evaluate_news({'text':'united\n states government export\tcontrol healthcare'})
    assert 'united states' in result['country_terms']
    assert 'export control' in result['government_terms']
    result=evaluate_news({'text':'xgovernment2 xexport2 xhealthcare2'})
    assert result['score']==0


def test_lens_normalization_and_boundaries_unchanged():
    from strategy_trends import matched_terms, _lens_pattern
    import unicodedata
    text='ＡＩ 정부 clinicals clinical axolotl ＡＸ'
    terms=['AI','AI 정부','clinical','AX','의료']
    normalized=unicodedata.normalize('NFKC',text).casefold()
    assert matched_terms({'text':text},terms)==[t for t in terms if _lens_pattern(t).search(normalized)]
    assert matched_terms({'text':text},[])==[]
