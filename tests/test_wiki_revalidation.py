"""Regression coverage for publication after a false-stale dependency check."""
import json
from copy import deepcopy

import pytest

from bulk_baseline import digest
from knowledge_wiki import KnowledgeWiki
from test_knowledge_wiki import setup, Runner
from wiki_revalidation import revalidate_latest
from wiki_sources import collect, current, collect_question
from wiki_structure import purpose


@pytest.fixture
def stale_wiki(tmp_path, monkeypatch):
    path = tmp_path / 'wiki.db'
    setup(path)
    runner = Runner()
    service = KnowledgeWiki(path, runner, enabled=True, start_worker=False)
    with monkeypatch.context() as m:
        m.setattr('knowledge_wiki.current', lambda *args: False)
        assert service.compile('medical') == 'stale'
    with service.db() as db:
        db.execute("UPDATE wiki_topics SET attempts=2 WHERE id='medical'")
    yield service, runner
    service.close()


def test_exact_review_revalidation_preserves_results_and_attempt_history(stale_wiki):
    service, runner = stale_wiki
    with service.db() as db:
        old = dict(db.execute('SELECT * FROM wiki_revisions').fetchone())
        attempts = db.execute('SELECT * FROM wiki_attempts').fetchall()
    calls = list(runner.calls)
    assert service.compile('medical') == 'complete'
    assert runner.calls == calls
    assert service.get('medical')['page']
    with service.db() as db:
        rows = db.execute('SELECT * FROM wiki_revisions ORDER BY id').fetchall()
        assert dict(rows[0]) == old
        assert rows[1]['result_json'] == old['result_json']
        assert json.loads(rows[1]['changes_json'])['revalidated_revision'] == old['id']
        assert db.execute('SELECT * FROM wiki_attempts').fetchall() == attempts
        topic = db.execute("SELECT * FROM wiki_topics WHERE id='medical'").fetchone()
        assert topic['attempts'] == 2 and topic['published_revision'] == rows[1]['id']
        event = db.execute("SELECT detail_json FROM wiki_setting_events WHERE kind='revalidation'").fetchone()
        assert json.loads(event[0])['model_calls'] == 0
    assert service.compile('medical') is None
    assert runner.calls == calls


@pytest.mark.parametrize('change', ['source', 'config', 'policy', 'review', 'report', 'bundle', 'latest_rejected'])
def test_revalidation_rejects_changed_inputs_and_unapproved_results(stale_wiki, change):
    service, _ = stale_wiki
    with service.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM wiki_revisions').fetchone()
        result = json.loads(row['result_json'])
        bundle = deepcopy(result['bundle'])
        if change == 'source':
            db.execute("UPDATE articles SET text='정정된 의료 연구'")
        elif change == 'config':
            from wiki_registry import configure
            configure(db, dict(topic='medical', enabled=False))
        elif change == 'policy':
            result['purpose_hash'] = 'old-policy'
        elif change == 'review':
            result['review']['checks'][0]['supported'] = False
        elif change == 'report':
            result['report']['pages'][0]['claims'][0]['text'] = '바뀐 주장'
        elif change == 'bundle':
            bundle['title'] = '다른 주제'
        else:
            db.execute("UPDATE wiki_revisions SET status='needs_review'")
        db.execute('UPDATE wiki_revisions SET result_json=?', (json.dumps(result),))
        before = db.execute('SELECT * FROM wiki_revisions').fetchall()
        assert not revalidate_latest(db, 'medical', bundle, digest(purpose()))
        assert db.execute('SELECT * FROM wiki_revisions').fetchall() == before
        assert not db.execute("SELECT 1 FROM wiki_setting_events WHERE kind='revalidation'").fetchone()


def test_manual_retry_still_runs_independent_review(stale_wiki):
    service, runner = stale_wiki
    before = len(runner.calls)
    service.request('medical')
    assert service.compile('medical') == 'complete'
    assert runner.calls[before:] == ['wiki_compile', 'wiki_review']


@pytest.mark.parametrize('collector', ['topic', 'question'])
@pytest.mark.parametrize('change', ['metadata', 'review', 'error', 'delete'])
def test_collected_paper_is_current_until_evidence_or_approval_changes(collector, change):
    import sqlite3
    from test_paper_graph import PaperGraphTests
    from wiki_registry import initialize, configure
    helper = PaperGraphTests()
    helper.setUp()
    try:
        result = helper.add()
        db = helper.db
        db.row_factory = sqlite3.Row
        db.execute('ALTER TABLE arxiv_paper_analyses ADD COLUMN attempts INTEGER DEFAULT 1')
        db.execute('CREATE TABLE wiki_topics(id TEXT PRIMARY KEY,requested INTEGER)')
        initialize(db)
        configure(db, dict(topic='robots', name='로봇', terms=['Physical AI']))
        if collector == 'topic':
            bundle = collect(db, 'robots')
        else:
            from link_groups import canonical_url
            bundle = collect_question(db, 'robots', dict(question='Physical AI 연구는?',
                urls_json=json.dumps([canonical_url(result['evidence'][0]['source_url'])])))
        assert len(bundle['evidence']) == 1
        assert current(db, bundle['evidence'])
        if change == 'metadata':
            metadata = json.loads(db.execute('SELECT metadata_json FROM arxiv_papers').fetchone()[0])
            metadata['abstract'] += ' Corrected results.'
            db.execute('UPDATE arxiv_papers SET metadata_json=?', (json.dumps(metadata),))
        elif change == 'review':
            result['verification']['accepted'] = False
            db.execute('UPDATE arxiv_paper_analyses SET result_json=?', (json.dumps(result),))
        elif change == 'error':
            db.execute("UPDATE arxiv_paper_analyses SET error='failed'")
        else:
            db.execute('DELETE FROM arxiv_paper_analyses')
        assert not current(db, bundle['evidence'])
    finally:
        helper.tearDown()
