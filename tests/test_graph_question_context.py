import json
import threading
import time
from concurrent.futures import CancelledError
from unittest.mock import patch

import pytest

from graph_question_context import compose, focus, document, requested_facets, focused_candidates
from graph_rag import GraphResult, answer_question


def evidence(identity, text=None, url=None):
    return dict(id=identity, title='벤치마크 데이터 평가', text=text or identity,
                source_url=url or 'https://example.org/'+identity, source_kind='external')


def group(items, nodes=None):
    return dict(evidence=items, nodes=nodes or [], edges=[])


def test_supplements_preserve_all_axes_and_do_not_crowd_out_last_axis(tmp_path):
    groups = [group([evidence(f'{axis}-{i}') for i in range(12)]) for axis in range(5)]
    paragraphs = [evidence('p'+str(i), 'additional benchmark detail '+str(i), groups[0]['evidence'][i]['source_url']) for i in range(8)]
    originals = json.dumps(groups)
    with patch('graph_rag.build_retrieval', side_effect=groups), patch('source_store.search', return_value=paragraphs):
        result = compose({}, '벤치마크 질문', [], dict(queries=['a','b','c','d']), tmp_path/'db', True)
    selected = result['evidence']
    assert len(selected) <= 20
    for axis in range(5):
        assert sum(e['id'].startswith(str(axis)+'-') for e in selected) >= 3
    assert max(sum(document(e)==document(other) for other in selected) for e in selected) <= 2
    assert json.dumps(groups) == originals


def test_contained_source_replacement_keeps_citations_and_scope(tmp_path):
    short = evidence('short', '벤치마크는 실험실에서만 검증했다.')
    full = evidence('full', '벤치마크는 실험실에서만 검증했다. 현장 성능은 확인하지 못했다.', short['source_url'])
    other = evidence('other', full['text'], short['source_url'])
    node = dict(id='n', evidence_ids=['short'])
    # Later query with no refs must not erase an earlier node's reference.
    groups = [group([short], [node]), group([other], [dict(id='n', evidence_ids=[])])]
    with patch('graph_rag.build_retrieval', side_effect=groups), patch('source_store.search', return_value=[full,short]) as search, patch('knowledge_wiki.retrieve') as wiki:
        result = compose({}, '벤치마크', ['n'], dict(queries=['현장 성능']), tmp_path/'db', True)
    assert result['evidence'] == [full]
    assert result['nodes'] == [dict(id='n', evidence_ids=['full'])]
    assert search.call_args.kwargs['urls'] == {short['source_url']}
    wiki.assert_not_called()


def test_focus_is_contiguous_and_keeps_qualifying_sentence():
    prefix = '이 문장은 배경 설명입니다. '*100
    target = '벤치마크 GPT-5.3의 성공률은 90%다. 단 실제 현장 성능으로 일반화할 수 없다. '
    original = evidence('full', prefix+target+'후속 배경 설명입니다. '*100)
    result = focus(original, '벤치마크 성공률', budget=300)
    assert target.strip() in result['text']
    assert result['text'] == original['text'][result['excerpt_start']:result['excerpt_end']]
    assert len(result['text']) < len(original['text'])/3
    assert original['text'].startswith(prefix)


def test_focus_preserves_incident_outcome_beyond_keyword_heavy_introduction():
    intro='AI security incidents and agent security research overview. '*28
    result_text='In September 2026 the attack leaked 53 images. However, the researchers did not establish wider exposure. '
    source=evidence('incident',intro+'General discussion of the industry. '*50+result_text+'Background context. '*100)
    result=focus(source,'최근 AI 보안 사고 AI security incidents agent injection')
    assert '53 images' in result['text'] and 'did not establish wider exposure' in result['text']
    segments=result['excerpt_segments']
    assert result['text']=='\n[중간 문맥 생략]\n'.join(source['text'][s['start']:s['end']] for s in segments)
    assert len(segments)<=3 and result['source_text_hash']


def test_layout_duplicates_are_removed_but_distinct_qualifications_survive(tmp_path):
    short=evidence('short','실험에서 90% 성공했다.\n현장 적용은 미검증이다.')
    same=evidence('layout','실험에서 90% 성공했다.   현장 적용은 미검증이다.',short['source_url'])
    other=evidence('other','다른 환경에서는 40% 성공했다. 현장 적용은 미검증이다.',short['source_url'])
    with patch('graph_rag.build_retrieval',return_value=group([short])),patch('source_store.search',return_value=[same,other]):
        result=compose({},'실험 성공률',[],dict(queries=[]),tmp_path/'db',True)
    assert {e['id'] for e in result['evidence']}=={'short','other'}


def test_full_review_sees_uncited_conflict_and_cache_tracks_full_context(monkeypatch):
    original = evidence('support', '배경입니다. '*300+'벤치마크 실험 성공률은 90%다. 현장 일반화는 미검증이다. '+'기타 배경입니다. '*300)
    conflict = evidence('conflict', '다른 환경에서 벤치마크 성공률은 40%였다.')
    bundle = group([original,conflict]); bundle.update(no_hits=False, limitations=[], coverage={
        'scope':'selected','coverage_basis':'lexical_retrieval_not_semantic_verification',
        'unique_documents':2,'passages':2,'full_corpus_analysis':False,
        'axes':[dict(axis='성공률',selected_documents=2,candidate_documents=20)]})
    graph = GraphResult(coverage={'all_corpus_documents':10}); graph.search_key='context-test'
    calls=[]
    def runner(prompt, schema, **kw):
        calls.append(prompt)
        if 'checks' in schema['properties']:
            data=json.loads(prompt[prompt.index('\n')+1:])
            assert {e['source_url']:e['text'] for e in data['evidence']} == {original['source_url']:original['text'],conflict['source_url']:conflict['text']}
            support_id=next(e['id'] for e in data['evidence'] if e['source_url']==original['source_url'])
            assert data['claims'][0]['evidence_ids']==[support_id]
            assert data['axes'] == ['성공률'] and data['as_of']
            value=dict(checks=[dict(claim_index=0,supported=True,reason='조건부 해석')],answer_supported=True,missing_information=[],citation_corrections=[])
            if 'quality' in schema['properties']:
                from test_answer_quality import audit
                count=schema['properties']['quality']['properties']['axes']['minItems']
                value['quality']=audit(axes=('addressed',)*count)['quality']
            return value
        data=json.JSONDecoder().raw_decode(prompt.split('\nDATA:\n')[1])[0]
        support_id=next(e['id'] for e in data['evidence'] if e['source_url']==original['source_url'])
        return dict(paragraphs=[[0]], claims=[dict(sentences=['실험 성공률90%, 현장은 미검증'], evidence_ids=[support_id],
                    kind='finding',axis='성공률',premises=None,assumptions=None,counterevidence=None,uncertainty=[0])],limitations=[])
    monkeypatch.setattr('semantic.run_structured', runner)
    def run():return answer_question(graph,'벤치마크 성공률',retrieval=bundle,analysis_plan=dict(axes=['성공률'],queries=['성공률']),detailed=True)
    first=run()
    assert first['claims'][0]['evidence_ids']==['support']
    assert first['retrieval']['generation_evidence_characters'] < first['retrieval']['review_evidence_characters']
    graph['coverage']['all_corpus_documents']=11
    bundle['coverage']['axes'][0]['candidate_documents']=30
    assert run()['timing']['answer_cache_hit'] and len(calls)==2
    bundle['evidence'].reverse()
    reused=answer_question(graph,'벤치마크 성공률',retrieval=bundle,analysis_plan=dict(axes=['성공률'],queries=['성공률']),detailed=True,reuse_only=first)
    assert reused['verification']==first['verification'] and len(calls)==2
    # A qualification outside the focused generation window still invalidates.
    original['text']+=' 추가로 다른 제약이 존재한다.'
    from graph_answer_cache import AnswerInputsChanged
    with pytest.raises(AnswerInputsChanged):
        answer_question(graph,'벤치마크 성공률',retrieval=bundle,analysis_plan=dict(axes=['성공률'],queries=['성공률']),detailed=True,reuse_only=first)
    assert len(calls)==2
    assert not run()['timing']['answer_cache_hit'] and len(calls)==4


def test_requested_facets_find_english_sources_without_filling_with_incidental_news():
    question='Physical AI 벤치마크와 데이터세트 종류는?'
    direct=dict(evidence('direct','reported limitations'),title='Physical AI benchmark and dataset')
    incidental=[dict(evidence(str(i),'Physical AI benchmarking is mentioned. benchmark examples'),title='일반 산업 기사') for i in range(8)]
    noise=dict(evidence('noise','Physical AI 기업 투자 소식'),title='Physical AI 투자')
    facets=requested_facets(question)
    selected=focused_candidates(incidental+[noise,direct],question,facets)
    assert selected==[direct]+incidental[:2]
    assert len(facets)==2
    assert not requested_facets('Physical AI 동향은?')


def test_obsolete_queued_snapshot_is_skipped_without_starting_child(tmp_path, monkeypatch):
    from graph_snapshot import GraphSnapshots
    service=GraphSnapshots(tmp_path/'news.db')
    monkeypatch.setattr(service,'key',lambda params:'current')
    try:
        with patch('subprocess.Popen') as spawn:
            with pytest.raises(CancelledError): service._prepare_disk('obsolete',{})
            spawn.assert_not_called()
    finally:service.close()


def test_obsolete_running_snapshot_stops_child_and_cleans_only_its_temp(tmp_path, monkeypatch):
    from graph_snapshot import GraphSnapshots
    service=GraphSnapshots(tmp_path/'news.db')
    revisions=iter(['old','new'])
    monkeypatch.setattr(service,'key',lambda params:next(revisions))
    temporary=service.directory/'old.123.tmp'; temporary.write_text('partial')
    unrelated=service.directory/'other.456.tmp'; unrelated.write_text('keep')
    class Child:
        pid=123
        stopped=False
        def poll(self):return 0 if self.stopped else None
        def terminate(self):self.stopped=True
        def wait(self,**kwargs):return 0
    child=Child()
    try:
        with patch('subprocess.Popen',return_value=child),patch('graph_snapshot.time.monotonic',side_effect=[0,3]):
            with pytest.raises(CancelledError):service._prepare_disk('old',{})
        assert child.stopped and not temporary.exists() and unrelated.exists()
        assert not service.ready
    finally:service.close()


def test_queued_revisions_coalesce_before_build(tmp_path,monkeypatch):
    from graph_snapshot import GraphSnapshots
    service=GraphSnapshots(tmp_path/'news.db'); revision=['r1']; gate=threading.Event()
    monkeypatch.setattr(service,'key',lambda params:revision[0])
    built=[]
    monkeypatch.setattr(service,'_prepare',lambda key,params:built.append(key))
    blocker=service.workspace_executor.submit(gate.wait,3)
    try:
        first=service.request({'workspace':['1']})[2]
        revision[0]='r2';second=service.request({'workspace':['1']})[2]
        revision[0]='r3';third=service.request({'workspace':['1']})[2]
        assert first.cancelled() and second.cancelled()
        gate.set();third.result(timeout=3)
        assert built==['r3']
    finally:gate.set();service.close()
