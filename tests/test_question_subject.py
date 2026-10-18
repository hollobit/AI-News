import sqlite3
from unittest.mock import patch

import pytest

from question_subject import subject, matches
from context_index import write, read, materialize, candidate_ids, expressions
from graph_question_context import compose, focused_candidates, requested_facets
from graph_rag import GraphResult, build_retrieval
from graph_quick_sources import cold_answer, setup_source_search
from tests.test_context_index import source
from tests.test_graph_questions import source_db


@pytest.mark.parametrize('question,name', [
    ('PhysAI-Bench의 연구 현황과 데이터세트 한계는?', 'PhysAI-Bench'),
    ('최근 "Physical AI Smart Spaces"의 평가 방법은?', 'Physical AI Smart Spaces'),
    ('AeroEval-R에서 확인한 성능은?', 'AeroEval-R'),
    ('Depth Suite 2에 대한 데이터세트 설명', 'Depth Suite 2'),
    ('최근 Physical AI 벤치마크 현황과 데이터세트 이슈는?', None),
    ('Physical AI의 벤치마크 현황은?', None),
    ('AeroEval-R의 결과를 OtherBench와 비교해 주세요', None),
    ('AeroEval-R의 사례 말고 다른 연구 현황은?', None),
    ('AeroEval-R의 연구 현황과 딥시크의 평가 방법은?', None),
    ('AeroEval-R의 연구 현황과 otherbench의 데이터세트는?', None),
    ('AeroEval-R의 다음 버전 출시 시기를 예측해 주세요', None),
    ('AI는 무엇인가?', None),
    ('What is the benchmark?', None),
])
def test_conservative_subject_scope(question, name):
    assert subject(question) == name


def test_literal_match_preserves_full_name_and_allows_korean_particles():
    assert matches('Physical AI Smart Spaces', 'physical  AI\nsmart spaces의 한계')
    assert not matches('Physical AI Smart Spaces', 'Physical Smart Spaces with AI')
    assert not matches('AeroEval-R', 'AeroEval-R2')
    assert not matches('AeroEval-R', 'SuperAeroEval-R')


def test_named_record_is_reserved_beyond_generic_kind_quota(tmp_path, monkeypatch):
    question='AeroEval-R의 연구 현황 평가 방법 데이터세트 한계는?'
    items=[source('external',str(i),'AeroEval-R2 연구 현황 평가 방법 데이터세트 한계',
                  '연구 현황 평가 방법 데이터세트 한계') for i in range(60)]
    items.append(source('external','target','AeroEval-R','Specific protocol and limitations.'))
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:(items,{}))
    path=tmp_path/'index.db'
    with sqlite3.connect(':memory:') as db:write(path,db,'rev')
    with sqlite3.connect(path) as db:
        target=db.execute("SELECT id FROM sources WHERE text LIKE '%Specific protocol%'").fetchone()[0]
        broad=expressions(question)[0]
        before=[r[0] for r in db.execute('SELECT rowid FROM search WHERE search MATCH ? ORDER BY bm25(search,3,1),rowid LIMIT 48',(broad,))]
        assert target not in before
        assert target in candidate_ids(db,[question])
    graph=materialize(read(path,'rev'),question)
    result=build_retrieval(graph,question)
    assert len(result['evidence']) == 1
    assert result['evidence'][0]['title'] == 'AeroEval-R'


def test_named_materialization_and_node_scope_have_separate_caches(tmp_path, monkeypatch):
    items=[source('paper','target','AeroEval-R','Research dataset protocol.'),
           source('paper','other','Other dataset','Research dataset limitations.')]
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:(items,{}))
    path=tmp_path/'index.db'
    with sqlite3.connect(':memory:') as db:write(path,db,'rev')
    graph=read(path,'rev');question='AeroEval-R의 research dataset 현황은?'
    narrow=materialize(graph,question)
    wide=materialize(graph,question,named_scope=False)
    assert {e['title'] for e in narrow.full_evidence}=={'AeroEval-R'}
    assert {e['title'] for e in wide.full_evidence}=={'AeroEval-R','Other dataset'}
    assert narrow.search_key != wide.search_key
    assert materialize(graph,question) is narrow
    assert materialize(graph,question,named_scope=False) is wide


def test_materialization_accepts_whitespace_but_not_reordered_names(tmp_path, monkeypatch):
    items=[source('paper','target','Aero  Smart\nSpaces','Dataset protocol.'),
           source('paper','other','Aero Spaces Smart','Dataset limitations.')]
    monkeypatch.setattr('graph_rag._analysis_sources',lambda db:(items,{}))
    path=tmp_path/'index.db'
    with sqlite3.connect(':memory:') as db:write(path,db,'rev')
    graph=materialize(read(path,'rev'),'Aero Smart Spaces의 데이터세트 현황은?')
    assert len(graph.full_evidence)==1
    assert graph.full_evidence[0]['title']=='Aero Smart Spaces'


def evidence(identity,title,text):
    return dict(id=identity,title=title,text=text,source_url='https://example.org/'+identity,source_kind='paper')


def test_named_scope_keeps_counterevidence_even_without_facet_words():
    question='AeroEval-R의 연구 현황과 데이터세트 한계는?'
    official=evidence('official','AeroEval-R','Dataset protocol; 90% in the laboratory.')
    qualification=evidence('critical','Independent assessment','AeroEval-R failed outdoors under these conditions.')
    unrelated=evidence('other','Other benchmark','Large dataset benchmark limitations.')
    graph=GraphResult(nodes=[],edges=[],evidence=[official,qualification,unrelated])
    selected=build_retrieval(graph,question)['evidence']
    selected=focused_candidates(selected,question,requested_facets(question))
    assert {e['id'] for e in selected} == {'official','critical'}
    assert selected[0]['text'] in (official['text'],qualification['text'])
    assert build_retrieval(graph,'AbsentBench의 데이터세트 한계는?')['no_hits']


def test_named_scope_does_not_override_explicit_graph_node():
    item=evidence('pinned','Separate observation','Unrelated protocol with a data qualification.')
    graph=GraphResult(nodes=[dict(id='n',name='Explicit selection',type='SourceDocument',evidence_ids=['pinned'])],edges=[],evidence=[item])
    result=build_retrieval(graph,'AeroEval-R의 데이터세트 현황은?',['n'])
    assert result['evidence'] == [item]
    assert focused_candidates([item],'AeroEval-R의 데이터세트 현황은?',[],named_scope=False)==[item]


def test_supplements_preserve_selected_document_context_but_exclude_unrelated(tmp_path):
    question='AeroEval-R의 연구 현황과 데이터세트 한계는?'
    original=evidence('first','AeroEval-R','Dataset protocol.')
    continuation=dict(evidence('rest','Continuation','However, only the laboratory was tested.'),source_url=original['source_url'])
    unrelated=evidence('other','Other dataset','Other research limitations.')
    with patch('graph_rag.build_retrieval',return_value=dict(nodes=[],edges=[],evidence=[original])), \
            patch('source_store.search',return_value=[continuation,unrelated]), \
            patch('knowledge_wiki.retrieve',return_value=[]):
        result=compose({},question,[],dict(queries=[],axes=['데이터세트']),tmp_path/'db',False)
    assert {e['id'] for e in result['evidence']} == {'first','rest'}
    assert result['coverage']['unique_documents'] == 1


def test_named_abstract_keeps_admitted_full_text_from_same_paper():
    from graph_retrieval import RetrievalIndex
    question='AeroEval-R의 연구 현황과 데이터세트 한계는?'
    abstract=evidence('abstract','AeroEval-R','Dataset evaluation protocol in the abstract.')
    full=dict(evidence('full','AeroEval-R','Additional experiment: a different condition failed.'),
              source_url=abstract['source_url'],evidence_origin='arxiv_html_excerpt')
    landing=dict(evidence('landing','AeroEval-R','Dataset protocol and download page navigation. '*10),source_url=abstract['source_url'])
    unrelated=evidence('other','Other paper','Other dataset protocol.')
    graph=GraphResult(nodes=[],edges=[],evidence=[abstract,landing,full,unrelated])
    graph.prepared_index=RetrievalIndex(graph)
    with patch('graph_rag.build_retrieval',return_value=dict(nodes=[],edges=[],evidence=[abstract])):
        result=compose(graph,question,[],dict(queries=[],axes=['데이터세트']))
    assert {e['id'] for e in result['evidence']}=={'abstract','full'}
    assert result['coverage']['unique_documents']==1
    assert next(e['text'] for e in result['evidence'] if e['id']=='full')==full['text']


@pytest.mark.parametrize('indexed',[False,True])
def test_cold_preview_uses_same_subject_and_preserves_filters(tmp_path,indexed):
    path=tmp_path/'news.db';source_db(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE articles SET title='Aero Smart Spaces',excerpt='Dataset protocol',text='Aero  Smart Spaces dataset protocol.'")
        db.execute("INSERT INTO articles SELECT chat_id,message_id,2,'Aero Smart SpacesX','Dataset','Dataset limitations',day,topic,'https://example.org/b',kind FROM articles")
    if indexed:setup_source_search(path)
    found=cold_answer(path,'Aero Smart Spaces의 데이터세트 현황은?',[],{})
    assert len(found['evidence'])==1 and found['evidence'][0]['source_url']=='https://example.org/a'
    assert cold_answer(path,'Aero Smart Spaces의 데이터세트 현황은?',[],{'channel':['wrong']}) is None
    assert cold_answer(path,'AbsentBench의 데이터세트 현황은?',[],{}) is None
