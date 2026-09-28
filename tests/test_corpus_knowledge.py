from copy import deepcopy
from corpus_knowledge import expand


def fixture():
    graph=dict(nodes=[],edges=[],coverage=dict(pages=0,cited_sources=0,all_sources_compiled=False),method='')
    corpus=dict(news=[dict(id='a',title='기사',url='https://example.org/a',day='2026-09-20',
        observation_document_id='doc',analyses=[dict(kind='기본 분석',text='검토 내용')]),
        dict(id='b',title='미검토',analyses=[])],papers=[dict(id='p',title='논문',day='',url='https://arxiv.org/abs/p',
        status='검토 완료',claims=[dict(title='주장',detail='검토 논문 내용')])])
    observation=dict(documents={'e':{'document_id':'doc'}},nodes=[dict(id='term',label='관측 용어',kind='keyword',document_ids_by_day=[['e']])])
    return graph,corpus,observation


def test_all_reviewed_documents_connected_without_semantic_invention():
    graph,corpus,observation=fixture()
    result=expand(graph,corpus,observation)
    assert result['coverage']['linked_news']==1
    assert result['coverage']['total_news']==2
    assert result['coverage']['linked_papers']==1
    assert result['coverage']['all_sources_compiled'] is False
    assert {e['layer'] for e in result['edges']}=={'provenance','recommendation'}
    ids={n['id'] for n in result['nodes']}
    assert all(e['source'] in ids and e['target'] in ids for e in result['edges'])
    assert not any(n['title']=='미검토' for n in result['nodes'])


def test_rebuild_removes_invalidated_analyses_and_stable_ids():
    graph,corpus,observation=fixture()
    original=expand(deepcopy(graph),corpus,observation)
    assert original==expand(deepcopy(graph),deepcopy(corpus),observation)
    corpus['news'][0]['analyses']=[]
    corpus['papers'][0]['status']='현재 공개 가능한 검토 결과 없음'
    result=expand(graph,corpus,observation)
    assert result['nodes']==[] and result['edges']==[]


def test_existing_wiki_source_reused_by_exact_news_identity():
    graph,corpus,observation=fixture()
    graph['nodes']=[dict(id='source:existing',type='source',news_id='a',title='기사')]
    result=expand(graph,corpus,observation)
    assert not any(n['id']=='source:news:a' for n in result['nodes'])
    assert any(e['source']=='source:existing' for e in result['edges'])
