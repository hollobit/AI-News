"""Full original-source review stays mandatory without an optional LLM prepass."""
import json

import pytest

from knowledge_wiki import KnowledgeWiki
from test_knowledge_wiki import setup, Runner
from wiki_sources import collect
from wiki_structure import key, editing_notes, purpose


@pytest.fixture
def service(tmp_path):
    path = tmp_path / 'wiki.db'
    setup(path)
    value = KnowledgeWiki(path, Runner(), enabled=True, start_worker=False)
    yield value
    value.close()


@pytest.mark.parametrize('corrupt', ['malformed_json', 'unknown_source', 'invented_quote'])
def test_invalid_optional_cache_does_not_block_full_source_review(service, corrupt):
    with service.db() as db:
        bundle = collect(db, 'medical')
        note = dict(type='claim',label='단서',evidence_id=bundle['evidence'][0]['id'],quote='발명된 문구')
        if corrupt == 'unknown_source':
            note.update(evidence_id='unknown',quote=bundle['evidence'][0]['text'])
        raw = '{invalid' if corrupt == 'malformed_json' else json.dumps({'notes':[note]})
        db.execute('INSERT INTO wiki_structures VALUES (?,?)', (key(bundle),raw))
        assert editing_notes(db,bundle) == {'notes':[]}
    assert service.compile('medical') == 'complete'
    assert service.runner.calls == ['wiki_compile','wiki_review']
    with service.db() as db:
        assert db.execute('SELECT result_json FROM wiki_structures').fetchone()[0] == raw
        result = json.loads(db.execute('SELECT result_json FROM wiki_revisions').fetchone()[0])
        assert result['bundle'] == bundle
        assert result['structure'] == {'notes':[]}
        assert result['generation_path'] == 'direct-wiki-v1'


def test_hints_are_draft_context_only_and_originals_reach_review(service, monkeypatch):
    prompts = []
    runner = service.runner
    def capture(prompt,schema,**kwargs):
        prompts.append((kwargs['role'],prompt))
        return runner(prompt,schema,**kwargs)
    service.runner = capture
    with service.db() as db:
        evidence = collect(db, 'medical')['evidence']
    monkeypatch.setattr('wiki_reuse.reviewed_hints', lambda *args: [dict(kind='baseline',
        text='별도 편집 단서 문구',evidence_ids=[evidence[0]['id']])])
    assert service.compile('medical') == 'complete'
    assert [p[0] for p in prompts] == ['wiki_compile','wiki_review']
    assert '별도 편집 단서 문구' in prompts[0][1]
    assert '별도 편집 단서 문구' not in prompts[1][1]
    payload = json.loads(prompts[1][1].split('\n')[-1])
    assert payload['evidence'] == evidence
    assert payload['purpose'] == purpose()
    assert '24개' in payload['scope']
    assert payload['topic'] == '의료'
    assert payload['pages'] == [dict(title='의료',kind='topic'),dict(title='의료 AI 연구 발표',kind='event')]


def test_coverage_rejection_uses_bounded_repair_and_is_not_published(service):
    # A factually correct fragment can still fail the whole-topic explanation.
    base = service.runner
    seen = []
    def missing(prompt,schema,**kwargs):
        value = base(prompt,schema,**kwargs)
        if kwargs['role'] == 'wiki_review':
            value['checks'][0].update(supported=False,reason='중요한 작동 방식과 적용 조건 누락')
        else:
            seen.append(prompt)
        return value
    service.runner = missing
    assert service.compile('medical') == 'needs_review'
    assert base.calls == ['wiki_compile','wiki_review'] * 2
    assert '중요한 작동 방식과 적용 조건 누락' in seen[1]
    assert service.get('medical')['page'] is None
    assert service.compile('medical') is None
    assert base.calls == ['wiki_compile','wiki_review'] * 2


@pytest.mark.parametrize('role', ['wiki_compile','wiki_review'])
def test_failed_stage_is_persisted_without_untrusted_error_text(service, role):
    runner = service.runner
    def fail(prompt,schema,**kwargs):
        if kwargs['role'] == role:
            raise ValueError('private provider response and credentials')
        return runner(prompt,schema,**kwargs)
    service.runner = fail
    assert service.compile('medical') == 'failed'
    with service.db() as db:
        error = db.execute("SELECT error FROM wiki_topics WHERE id='medical'").fetchone()[0]
        assert f'[wiki:{role}]' in error
        assert 'private provider' not in error and 'credentials' not in error
        assert db.execute('SELECT error FROM wiki_attempts').fetchone()[0] == error
        assert db.execute('SELECT COUNT(*) FROM wiki_revisions').fetchone()[0] == 0


def test_infrastructure_diagnostics_remain_machine_readable():
    from engine_errors import EngineError,infrastructure_error
    from wiki_errors import describe
    message = describe(EngineError('network'), 'wiki_review')
    assert '[wiki:wiki_review]' in message
    assert infrastructure_error(message) == 'network'


def test_changed_purpose_cannot_reuse_old_notes(service, monkeypatch):
    with service.db() as db:
        bundle = collect(db, 'medical')
        source = bundle['evidence'][0]
        notes = dict(notes=[dict(type='claim',label='단서',evidence_id=source['id'],quote=source['text'])])
        db.execute('INSERT INTO wiki_structures VALUES (?,?)',(key(bundle),json.dumps(notes)))
        assert editing_notes(db,bundle) == notes
        monkeypatch.setattr('wiki_structure.purpose', lambda: '변경된 위키 목적')
        assert editing_notes(db,bundle) == {'notes':[]}
