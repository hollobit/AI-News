import json
import os
import sqlite3
from copy import deepcopy
import pytest
from knowledge_wiki import KnowledgeWiki, validate, verified, read_wiki, retrieve, page_id
from wiki_sources import collect, current


def setup(path):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE articles(chat_id TEXT,message_id INTEGER,item_index INTEGER,title TEXT,text TEXT,excerpt TEXT,day TEXT,source_url TEXT)')
        db.execute("INSERT INTO articles VALUES ('1',1,0,'의료 AI 연구 발표','연구팀은 의료 AI의 연구 결과를 발표했다.','연구 결과','2026-09-18','https://example.org/medical')")


def report(bundle):
    ref = bundle['evidence'][0]['id']
    claim = {'text': '연구팀이 의료 AI 연구 결과를 발표했다고 보도됐다.', 'kind': 'reported', 'evidence_ids': [ref]}
    return {'pages': [{'title': bundle['title'], 'kind': 'topic', 'claims': [claim]},
                      {'title': '의료 AI 연구 발표', 'kind': 'event', 'claims': [dict(claim)]}],
            'links': [{'source': bundle['title'], 'target': '의료 AI 연구 발표', 'relation': 'involves',
                       'text': '이 주제에서 연구 발표가 보도됐다.', 'evidence_ids': [ref]}]}


class Runner:
    def __init__(self):
        self.calls = []; self.reject = False; self.change = None; self.duplicate = False

    def __call__(self, prompt, schema, **kwargs):
        self.calls.append(kwargs['role'])
        if kwargs['role'] == 'wiki_structure':return {'notes':[]}
        if kwargs['role'] == 'wiki_compile':
            bundle = json.loads(prompt.split('\nDATA:')[1].split('\nPREVIOUS:')[0])
            return report(bundle)
        if self.change:
            self.change()
        count = schema['properties']['checks']['minItems']
        return {'checks': [{'index': 0 if self.duplicate else i, 'supported': not self.reject, 'reason': '근거 대조'} for i in range(count)]}


@pytest.fixture
def wiki(tmp_path):
    path = tmp_path / 'news.db'; setup(path); runner = Runner()
    service = KnowledgeWiki(path, runner, enabled=True, start_worker=False)
    yield service, runner
    service.close()


def test_compile_persist_navigation_and_reuse(wiki):
    service, runner = wiki
    assert service.compile('medical') == 'complete'
    data = service.get('medical')
    assert data['page']['title'] == '의료'
    assert len(data['pages']) == 2 and len(data['links']) == 1
    child = next(p for p in data['pages'] if p['kind'] == 'event')
    assert service.get(child['id'])['page']['claims']
    assert len(data['history'][0]['changes']['added']) == 1
    assert len(service.get(source_url='https://example.org/medical')['pages']) == 2
    assert service.get(source_url='https://example.org/unknown')['pages'] == []
    service.compile('medical')
    assert len(runner.calls) == 3
    reopened = KnowledgeWiki(service.path, runner, enabled=False, start_worker=False)
    assert reopened.get('medical')['page']
    assert len(reopened.get(query='연구 발표')['pages']) == 1
    reopened.close()


def test_manage_custom_topic_history_disable_and_export(wiki):
    service, _ = wiki
    config = service.manage(dict(topic='clinical', name='임상 연구', terms=['의료 AI'], historical=True))
    assert config['historical']
    assert service.compile('clinical') == 'complete'
    assert service.get('clinical')['page']['title'] == '임상 연구'
    exported = service.export()
    assert '임상 연구' in exported and 'https://example.org/medical' in exported
    service.manage(dict(topic='clinical', enabled=False))
    assert service.get('clinical')['page'] is None
    assert '## 임상 연구' not in service.export()
    with pytest.raises(ValueError):service.request('clinical')
    service.manage(dict(topic='clinical', enabled=True, name='의료 연구 변경'))
    assert service.get('clinical')['page'] is None
    assert service.compile('clinical') == 'complete'
    assert len(service.get('clinical')['history']) == 2


@pytest.mark.parametrize('payload', [
    dict(topic='../bad', name='예시', terms=['test']),
    dict(topic='good', name='예시', terms=[]),
    dict(topic='good', name='예시', terms=['x']),
    dict(topic='good', name='예시', terms=['test'], enabled='false'),
])
def test_invalid_wiki_configuration(wiki, payload):
    with pytest.raises(ValueError):wiki[0].manage(payload)


def test_historical_sources_and_configuration_race(wiki):
    service, runner = wiki
    with service.db() as db:
        db.executemany('INSERT INTO articles VALUES (?,?,?,?,?,?,?,?)', [
            ('1', i, 0, '의료 AI', '의료 AI 자료 ' + str(i), '', '2026-08-%02d' % i, 'https://example.org/' + str(i)) for i in range(2, 25)])
        normal = collect(db, 'medical')
    assert len(normal['evidence']) == 12
    service.manage(dict(topic='medical', historical=True))
    with service.db() as db:expanded = collect(db, 'medical')
    assert len(expanded['evidence']) == 20
    assert min(e['day'] for e in expanded['evidence']) < min(e['day'] for e in normal['evidence'])
    runner.change = lambda: service.manage(dict(topic='medical', enabled=False))
    assert service.compile('medical') == 'stale'
    assert service.get('medical')['page'] is None


def test_export_escapes_untrusted_markdown():
    from wiki_registry import export_markdown
    view = dict(pages=[dict(title='<script>bad</script>', revision=1, scope='[x](javascript:bad)', claims=[], evidence=[])], links=[])
    output = export_markdown(view)
    assert '<script>' not in output and '[x](javascript:' not in output


def test_cross_topic_alias_is_reversible_and_not_evidence(wiki):
    service, runner = wiki
    def entities(prompt, schema, **kwargs):
        value = runner(prompt, schema, **kwargs)
        if kwargs['role'] == 'wiki_compile':value['pages'][1]['kind'] = 'entity'
        return value
    service.runner = entities
    service.manage(dict(topic='clinical', name='임상', terms=['의료 AI']))
    for topic in ('medical', 'clinical'):assert service.compile(topic) == 'complete'
    pages = [p for p in service.get()['pages'] if p['kind'] == 'entity']
    payload = dict(source=pages[0]['id'], target=pages[1]['id'], reason='동일 원문의 동일 연구 대상')
    before = service.get()
    service.alias(payload)
    after = service.get()
    assert len(after['aliases']) == 1
    assert after['pages'] == before['pages'] and after['links'] == before['links']
    service.alias(dict(payload, remove=True))
    assert service.get()['aliases'] == []
    with service.db() as db:
        assert db.execute("SELECT count(*) FROM wiki_setting_events WHERE kind='alias'").fetchone()[0] == 2


def test_changed_deleted_source_hidden_and_history_preserved(wiki):
    service, _ = wiki
    service.compile('medical')
    with service.db() as db:
        db.execute("UPDATE articles SET text='의료 AI 연구 결과가 정정됐다.'")
    assert service.get('medical')['page'] is None
    assert next(t for t in service.get()['topics'] if t['id'] == 'medical')['status'] == 'stale'
    service.compile('medical')
    data = service.get('medical')
    assert len(data['history']) == 2
    assert data['history'][0]['changes']['changed']
    with service.db() as db:
        db.execute('DELETE FROM articles')
    assert service.get('medical')['page'] is None
    assert service.compile('medical') == None


def test_rejected_review_never_published_and_no_unbounded_retry(wiki):
    service, runner = wiki; runner.reject = True
    assert service.compile('medical') == 'needs_review'
    assert service.get('medical')['page'] is None
    assert service.get('medical')['history'][0]['status'] == 'needs_review'
    service.compile('medical'); assert len(runner.calls) == 5
    runner.reject = False; service.request('medical')
    assert service.compile('medical') == 'complete'


def test_publication_race_checks_inputs_again(wiki):
    service, runner = wiki
    def change():
        with service.db() as db:
            db.execute("UPDATE articles SET excerpt='변경된 발췌'")
    runner.change = change
    assert service.compile('medical') == 'stale'
    assert service.get('medical')['page'] is None


def test_invalid_refs_links_orphan_and_duplicate_audits(wiki):
    service, runner = wiki
    with service.db() as db: bundle = collect(db, 'medical')
    draft = report(bundle)
    for mutate in (lambda d: d['pages'][0]['claims'][0].update(evidence_ids=['invented']),
                   lambda d: d.update(links=[]),
                   lambda d: d['links'][0].update(target='missing'),
                   lambda d: d['pages'].append(deepcopy(d['pages'][1]))):
        invalid = deepcopy(draft); mutate(invalid)
        with pytest.raises(ValueError): validate(invalid, bundle)
    runner.duplicate = True
    assert service.compile('medical') == 'failed'
    assert service.get('medical')['page'] is None


def test_tampered_audit_or_report_not_reused(wiki):
    service, _ = wiki; service.compile('medical')
    with service.db() as db:
        row = db.execute('SELECT id,result_json FROM wiki_revisions').fetchone()
        value = json.loads(row['result_json']); assert verified(value)
        value['report']['pages'][0]['claims'][0]['text'] = '조작된 주장'
        assert not verified(value)
        db.execute('UPDATE wiki_revisions SET result_json=? WHERE id=?', (json.dumps(value), row['id']))
    assert service.get('medical')['page'] is None


def test_original_sources_only_for_graph_and_stale_excluded(wiki):
    service, _ = wiki; service.compile('medical')
    with service.db() as db:
        evidence = retrieve(db, '의료 연구 발표')
        assert evidence and all(e['origin'] == 'telegram_excerpt' for e in evidence)
        assert all('dependency' in e and 'wiki_revision' in e for e in evidence)
        db.execute("UPDATE articles SET text='철회된 의료 연구'")
        assert retrieve(db, '의료 연구 발표') == []


def test_dedup_news_and_source_health_changes(wiki):
    service, _ = wiki
    with service.db() as db:
        db.execute('INSERT INTO articles SELECT chat_id,2,item_index,title,text,excerpt,day,source_url FROM articles')
        db.execute('CREATE TABLE source_health(url,hash,last_status)')
        db.execute('CREATE TABLE source_versions(url,hash,title,text,scope)')
        db.execute("INSERT INTO source_health VALUES ('https://example.org/medical','v1','fetched')")
        db.execute("INSERT INTO source_versions VALUES ('https://example.org/medical','v1','원문','의료 연구의 원문','full')")
        bundle = collect(db, 'medical')
        assert len(bundle['evidence']) == 1
        assert bundle['evidence'][0]['origin'] == 'fetched_url_excerpt'
        assert current(db, bundle['evidence'])
        db.execute("UPDATE source_health SET last_status='failed'")
        assert not current(db, bundle['evidence'])


def test_live_owner_not_duplicated_and_disabled_gate(wiki):
    service, runner = wiki
    with service.db() as db:
        db.execute("UPDATE wiki_topics SET status='running',owner_pid=? WHERE id='medical'", (os.getpid(),))
    service.compile('medical'); assert runner.calls == []
    other = KnowledgeWiki(service.path, runner, enabled=False, start_worker=False)
    with pytest.raises(ValueError): other.request('medical')
    with pytest.raises(ValueError): service.request('unknown')
    other.close()


def test_stable_ids_casefold():
    assert page_id('medical', 'entity', 'ＡＩ   Lab') == page_id('medical', 'entity', 'ai lab')
    assert page_id('medical', 'entity', 'AI') != page_id('medical', 'event', 'AI')


def test_failed_draft_prose_is_not_exposed_in_history(wiki):
    service, runner = wiki; runner.reject = True
    service.compile('medical')
    history = service.get('medical')['history']
    assert history and 'claims_added' not in history[0]['changes']
    with service.db() as db:
        result = json.loads(db.execute('SELECT result_json FROM wiki_revisions').fetchone()[0])
        assert len(result['repair_history']) == 1
        assert not result['repair_history'][0]['review']['passed']


def test_paper_change_invalidates_with_shared_validation(wiki):
    from unittest.mock import patch
    from bulk_baseline import digest
    service, _ = wiki
    dep = {'kind': 'paper', 'key': '2609.00001', 'token': digest({'input_hash':'old'})}
    with service.db() as db, patch('wiki_sources.paper_token', return_value=dep['token']) as read:
        memo = {}
        assert current(db, [{'dependency':dep}], memo)
        assert current(db, [{'dependency':dep}], memo)
        assert read.call_count == 1
    with service.db() as db, patch('wiki_sources.paper_token', return_value=None):
        assert not current(db, [{'dependency':dep}])


def test_retains_current_old_evidence_and_does_not_ingest_wiki_text(wiki):
    service, _ = wiki
    with service.db() as db:
        first = collect(db, 'medical')
        for n in range(2,20):
            db.execute("INSERT INTO articles VALUES ('1',?,0,'의료 새 보도','의료 AI 새 자료','','2026-09-19',?)", (n,'https://example.org/'+str(n)))
        second = collect(db, 'medical', first)
        assert len(second['evidence']) == 13
        assert first['evidence'][0]['id'] in {e['id'] for e in second['evidence']}
        assert all(e['origin']=='telegram_excerpt' for e in second['evidence'])


def test_graph_hints_are_separate_and_review_only_sees_originals():
    from unittest.mock import patch
    from graph_rag import answer_question
    evidence = {'id':'original', 'title':'의료 연구', 'text':'연구팀은 의료 AI 결과를 보고했다.',
                'source_url':'https://example.org/a', 'wiki_context':[{'text':'이전 위키 설명', 'evidence_ids':['original']},
                {'text':'인용 누락 힌트', 'evidence_ids':['missing']}]}
    retrieval = {'nodes':[], 'edges':[], 'evidence':[evidence], 'no_hits':False, 'limitations':[]}
    prompts = []
    def run(prompt, schema, **kwargs):
        prompts.append(prompt)
        if len(prompts)==1:
            return {'answer':'연구팀이 결과를 보고했다.', 'claims':[{'text':'연구팀이 결과를 보고했다.', 'evidence_ids':['original']}], 'limitations':[]}
        return {'checks':[{'claim_index':0,'supported':True,'reason':'원문 일치'}], 'answer_supported':True, 'missing_information':[]}
    with patch('semantic.run_structured',side_effect=run):
        answer_question({'coverage':{}},'의료 연구 내용은?',retrieval=retrieval)
    assert '이전 위키 설명' in prompts[0] and '인용 누락 힌트' not in prompts[0]
    assert '이전 위키 설명' not in prompts[1] and '연구팀은 의료 AI 결과를 보고했다.' in prompts[1]


def test_single_paper_gate_matches_existing_validated_source():
    import test_paper_graph
    from wiki_sources import paper_token
    from paper_graph import paper_sources
    from bulk_baseline import digest
    helper = test_paper_graph.PaperGraphTests()
    helper.setUp()
    try:
        helper.add()
        helper.db.row_factory = sqlite3.Row
        source = paper_sources(helper.db)[0][0]
        assert paper_token(helper.db, source['id']) == digest(source['row'])
        helper.db.execute("UPDATE arxiv_paper_analyses SET status='needs_review'")
        assert paper_token(helper.db, source['id']) is None
    finally:
        helper.tearDown()
