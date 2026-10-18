"""Adaptive selection must improve coverage without relaxing source/review gates."""
from unittest.mock import patch

from graph_question_context import compose, document
from graph_rag import GraphResult, build_retrieval


def source(key, axis, *, text=None, url=None, kind='external'):
    return dict(id=key, title=f'Physical AI {axis}',
                text=text or f'Physical AI {axis}: observation {key}.',
                source_url=url or f'https://example.org/{key}', source_kind=kind)


def group(items):
    return dict(evidence=items, nodes=[], edges=[])


def compose_groups(groups, question, axes, path=None):
    queries=[f'axis {i}' for i in range(len(groups)-1)]
    with patch('graph_rag.build_retrieval', side_effect=groups):
        return compose({}, question, [], dict(queries=queries, axes=axes), path, True)


def test_compound_question_expands_missing_axes_past_twenty_passages():
    groups=[group([source(f'{axis}-{i}', axis) for i in range(14)])
            for axis in ('benchmark', 'dataset', 'issues')]
    result=compose_groups(groups, 'Physical AI 벤치마크 데이터세트 이슈는?',
                          ['벤치마크', '데이터세트', '이슈'])
    assert len(result['evidence']) == 24
    assert result['coverage']['unique_documents'] == 24
    assert result['retrieval']['candidate_documents'] == 42
    assert result['retrieval']['initial_documents'] == 16
    assert result['retrieval']['expansion_reasons'] == ['axis_coverage']
    assert all(a['selected_documents'] == 8 for a in result['coverage']['axes'])
    assert not result['coverage']['full_corpus_analysis']


def test_single_question_does_not_fill_larger_budget_and_sparse_axis_is_visible():
    benchmark=[source(f'b{i}', 'benchmark') for i in range(30)]
    simple=compose_groups([group(benchmark)], 'Physical AI 벤치마크는?', ['벤치마크'])
    assert len(simple['evidence']) == 16
    assert simple['retrieval']['budget']['passages'] == 20
    sparse=compose_groups([group(benchmark), group([])],
                          'Physical AI 벤치마크와 데이터세트는?', ['벤치마크','데이터세트'])
    assert len(sparse['evidence']) == 16
    missing=sparse['coverage']['axes'][1]
    assert missing['status'] == 'no_candidates' and missing['selected_documents'] == 0


def test_reposts_do_not_crowd_out_distinct_conditions_or_original_material_types():
    text='Physical AI benchmark: success rate 90%, only in the laboratory. ' * 4
    copied=[source(f'copy{i}', 'benchmark', text=text, kind='baseline') for i in range(25)]
    conflict=source('conflict', 'benchmark', text=text.replace('90%', '40%')+' However, field use failed.', kind='paper')
    result=compose_groups([group(copied+[conflict])], 'Physical AI 벤치마크', ['벤치마크'])
    assert {e['id'] for e in result['evidence']} == {'copy0', 'conflict'}
    assert result['retrieval']['candidate_documents'] == 26
    assert result['retrieval']['duplicate_passages'] == 24
    assert conflict in result['evidence']


def test_unselected_qualification_and_paper_are_added_for_compound_question():
    items=[source(f'b{i}', 'benchmark dataset', kind='baseline') for i in range(24)]
    qualification=source('qualified', 'benchmark dataset', text='Physical AI benchmark dataset: however, field reliability remains unverified.', kind='baseline')
    paper=source('official', 'benchmark dataset', text='Physical AI benchmark dataset protocol in the official abstract.', kind='paper')
    result=compose_groups([group(items+[qualification,paper])],
                          'Physical AI 벤치마크 데이터세트', ['벤치마크', '데이터세트'])
    assert qualification in result['evidence'] and paper in result['evidence']
    assert set(result['retrieval']['expansion_reasons']) == {'qualification_clues', 'source_types'}


def test_character_budget_excludes_whole_passages_without_cutting_conditions():
    items=[source(f'b{i}', 'benchmark', text=f'Physical AI benchmark {i}. '+('Full conditions. '*400)) for i in range(20)]
    result=compose_groups([group(items)], 'Physical AI 벤치마크', ['벤치마크'])
    assert result['retrieval']['budget_limited']
    assert sum(len(e['text']) for e in result['evidence']) <= result['retrieval']['budget']['characters']
    originals={e['id']:e['text'] for e in items}
    assert all(e['text'] == originals[e['id']] for e in result['evidence'])


def test_source_supplements_keep_document_scope_and_actual_counts(tmp_path):
    first=source('first', 'benchmark', text='Physical AI benchmark succeeded in a laboratory.')
    second=source('second', 'benchmark', text='Physical AI benchmark failed outdoors.', url=first['source_url'])
    with patch('source_store.search', return_value=[second]) as search:
        result=compose_groups([group([first])], 'Physical AI 벤치마크', ['벤치마크'], tmp_path/'db')
    assert search.call_args.kwargs['urls'] == {first['source_url']}
    assert result['coverage']['unique_documents'] == 1 and result['coverage']['passages'] == 2
    assert {document(e) for e in result['evidence']} == {first['source_url']}


def test_expanded_retrieval_removes_twelve_item_bottleneck_but_keeps_default():
    evidence=[source(f'paper{i}', 'benchmark') for i in range(35)]
    graph=GraphResult(nodes=[], edges=[], evidence=evidence)
    assert len(build_retrieval(graph, 'Physical AI benchmark')['evidence']) == 12
    assert len(build_retrieval(graph, 'Physical AI benchmark', evidence_limit=32)['evidence']) == 32


def test_corrected_citations_count_only_actual_unique_documents():
    from graph_evidence_selection import citation_counts
    first=source('one','benchmark')
    second=source('two','benchmark',url=first['source_url'])
    third=source('three','benchmark')
    result={'claims':[{'evidence_ids':['one','two','unknown']},{'evidence_ids':['two']}]}
    assert citation_counts(result,[first,second,third],document) == {'cited_passages':2,'cited_documents':1}


def test_retrieval_does_not_spend_document_budget_on_same_url_observations():
    items=[dict(source(f'observation{i}','benchmark',url='https://example.org/same'),document_id=f'row{i}') for i in range(20)]
    items += [source(f'unique{i}','benchmark') for i in range(20)]
    result=build_retrieval(GraphResult(nodes=[],edges=[],evidence=items),'Physical AI benchmark',evidence_limit=24)
    assert len(result['evidence']) == 21
    assert len({document(e) for e in result['evidence']}) == 21


def test_over_budget_no_hits_keeps_diagnostics_and_does_not_call_model():
    from graph_rag import answer_question
    huge=source('huge','benchmark',text='Physical AI benchmark. '+'conditions '*10000)
    retrieval=compose_groups([group([huge])], 'Physical AI 벤치마크', ['벤치마크'])
    with patch('semantic.run_structured') as model:
        result=answer_question({}, 'Physical AI 벤치마크', retrieval=retrieval)
    model.assert_not_called()
    assert '상한' in result['answer'] and result['retrieval']['candidate_documents'] == 1
    assert result['retrieval']['cited_documents'] == 0


def test_full_source_can_reveal_an_axis_missing_from_index_excerpt(tmp_path):
    short=source('short','benchmark',text='Physical AI benchmark protocol.')
    full=source('full','benchmark',text=short['text']+' Physical AI dataset and risks are described.',url=short['source_url'])
    with patch('source_store.search',return_value=[full]):
        result=compose_groups([group([short])],'Physical AI 벤치마크 데이터세트 이슈',
                              ['벤치마크','데이터세트','이슈'],tmp_path/'db')
    for axis in result['coverage']['axes']:
        assert axis['candidate_documents'] >= axis['selected_documents']
        assert axis['status'] != 'no_candidates'


def test_relational_synthesis_is_not_a_literal_keyword_coverage_quota():
    from graph_evidence_selection import axes_for
    from graph_question_context import requested_facets
    question='Physical AI 벤치마크와 데이터세트 이슈'
    plan={'axes':['벤치마크','데이터세트','이슈','연구 간 공통점·상충 근거·의존 관계와 시사점']}
    axes=axes_for(question,plan,requested_facets(question))
    assert [a['label'] for a in axes] == ['벤치마크','데이터세트','이슈']
    assert len(plan['axes']) == 4  # Independent review still receives every requested axis.
