import json
import sqlite3
from unittest.mock import patch

import pytest

from arxiv_papers import _paper_rows, init_papers
from context_paper_delta import install
from paper_analysis import analysis_input_hash
from paper_pipeline_inputs import PaperInputIndex


def database(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    init_papers(db)
    db.execute('CREATE TABLE arxiv_paper_analyses(paper_id TEXT PRIMARY KEY,input_hash TEXT,status TEXT)')
    install(db)
    for identity in ('2610.10000', '2610.10001'):
        db.execute("INSERT INTO arxiv_papers VALUES (?,?,'fetched','','')",
                   (identity, json.dumps(dict(title=identity, abstract='Original abstract', authors=[], categories=[]))))
    db.commit()
    return db


def expected(db):
    return [(p['paper_id'], p['metadata_status'], analysis_input_hash(p))
            for p in sorted(_paper_rows(db), key=lambda p: p['paper_id'], reverse=True)]


def actual(index, db):
    return [(p.paper_id, p.metadata_status, p.input_hash) for p in index.current(db)]


def test_unchanged_inputs_skip_hydration_and_sector_classification(tmp_path):
    with database(tmp_path/'db') as db:
        index = PaperInputIndex()
        assert actual(index, db) == expected(db)
        with patch('arxiv_papers._paper_rows', side_effect=AssertionError('unchanged papers hydrated')):
            assert len(index.current(db)) == 2


def test_only_changed_papers_refresh_and_deletions_disappear(tmp_path):
    with database(tmp_path/'db') as db:
        index = PaperInputIndex(); index.current(db)
        db.execute("UPDATE arxiv_papers SET metadata_json=? WHERE paper_id='2610.10000'",
                   (json.dumps(dict(title='Revised title', abstract='New reported result')),))
        db.commit()
        with patch('arxiv_papers._paper_rows', wraps=_paper_rows) as hydrate:
            assert actual(index, db) == expected(db)
            assert hydrate.call_args_list[0].args[1] == ['2610.10000']
        db.execute("DELETE FROM arxiv_papers WHERE paper_id='2610.10001'")
        db.commit()
        assert actual(index, db) == expected(db)
        assert len(index.current(db)) == 1


def test_mention_title_fallback_and_metadata_status_are_current(tmp_path):
    with database(tmp_path/'db') as db:
        db.execute("UPDATE arxiv_papers SET metadata_json='{}',status='pending' WHERE paper_id='2610.10000'")
        db.commit()
        index = PaperInputIndex(); previous = index.current(db)[1]
        db.execute("INSERT INTO arxiv_paper_mentions VALUES('2610.10000','https://arxiv.org/abs/2610.10000',NULL,'2026-10-18','Mention title','chat:1')")
        db.execute("UPDATE arxiv_papers SET status='fetched' WHERE paper_id='2610.10000'")
        db.commit()
        assert actual(index, db) == expected(db)
        current = index.current(db)[1]
        assert current.input_hash != previous.input_hash and current.metadata_status == 'fetched'


@pytest.mark.parametrize('invalidate', ['epoch', 'gap', 'overflow', 'missing_trigger', 'no_journal'])
def test_unprovable_history_forces_full_read(tmp_path, invalidate):
    with database(tmp_path/'db') as db:
        index = PaperInputIndex(); index.current(db)
        if invalidate == 'missing_trigger':
            db.execute('DROP TRIGGER paper_search_v1_arxiv_papers_update')
        if invalidate == 'no_journal':
            db.execute('DROP TABLE paper_search_state')
        for i in range(257 if invalidate == 'overflow' else 1):
            db.execute("UPDATE arxiv_papers SET metadata_json=? WHERE paper_id='2610.10000'",
                       (json.dumps(dict(title='Changed '+str(i))),))
        if invalidate == 'epoch':
            db.execute("UPDATE paper_search_state SET epoch='new'")
        elif invalidate == 'gap':
            db.execute('DELETE FROM paper_search_changes WHERE seq=(SELECT max(seq) FROM paper_search_changes)')
            db.execute("UPDATE arxiv_papers SET fetched_at='new'")
        db.commit()
        with patch('arxiv_papers._paper_rows', wraps=_paper_rows) as hydrate:
            got = actual(index, db)
            assert hydrate.call_args.args[1] is None
        assert got == expected(db)


def test_tentative_input_does_not_poison_committed_cache(tmp_path):
    with database(tmp_path/'db') as db:
        index = PaperInputIndex(); committed = actual(index, db)
        db.execute("UPDATE arxiv_papers SET metadata_json='{}'")
        assert actual(index, db) == expected(db) != committed
        db.rollback()
        assert actual(index, db) == committed


def test_read_snapshot_does_not_skip_concurrent_source_change(tmp_path):
    path = tmp_path/'db'
    with database(path) as db:
        index = PaperInputIndex(); original = expected(db)
        def read_and_change(connection, identities):
            result = _paper_rows(connection, identities)
            with sqlite3.connect(path) as writer:
                writer.execute("UPDATE arxiv_papers SET metadata_json='{}' WHERE paper_id='2610.10000'")
            return result
        with patch('arxiv_papers._paper_rows', side_effect=read_and_change):
            assert actual(index, db) == original
        assert actual(index, db) == expected(db) != original


def test_failed_refresh_does_not_advance_cursor(tmp_path):
    with database(tmp_path/'db') as db:
        index = PaperInputIndex(); index.current(db); old = index.position
        db.execute("UPDATE arxiv_papers SET metadata_json='broken' WHERE paper_id='2610.10000'")
        db.commit()
        with pytest.raises(ValueError):
            index.current(db)
        assert index.position == old and not db.in_transaction
        db.execute("UPDATE arxiv_papers SET metadata_json='{}' WHERE paper_id='2610.10000'")
        db.commit()
        assert actual(index, db) == expected(db)


def test_input_cache_does_not_freeze_retry_clock_or_approval(tmp_path):
    from test_paper_realtime import fixture_pipeline
    from engine_errors import EngineError
    pipeline, analysis = fixture_pipeline(tmp_path)
    try:
        analysis.submit(['2610.10000'])
        with pipeline.db() as db:
            db.execute("UPDATE arxiv_paper_analyses SET status='failed',error=?,updated_at='2026-01-01'",
                       (str(EngineError('network')),))
            db.execute("INSERT INTO paper_analysis_recovery VALUES('2610.10000',(SELECT input_hash FROM arxiv_paper_analyses),1,1001,'network')")
        with patch('paper_pipeline.time.time', return_value=1000):
            pipeline.tick()
        with pipeline.db() as db:
            assert db.execute('SELECT status FROM arxiv_paper_analyses').fetchone()[0] == 'failed'
        # No source change; the same cached fingerprint must still retry when due.
        with patch('paper_pipeline.time.time', return_value=1002):
            pipeline.tick()
        with pipeline.db() as db:
            assert db.execute('SELECT status FROM arxiv_paper_analyses').fetchone()[0] == 'queued'
            assert db.execute('SELECT attempts FROM paper_analysis_recovery').fetchone()[0] == 2
            # A rejected independent review is not accepted or automatically retried.
            db.execute("UPDATE arxiv_paper_analyses SET status='needs_review',error='unverified claim'")
        pipeline.tick()
        with pipeline.db() as db:
            assert db.execute('SELECT status FROM arxiv_paper_analyses').fetchone()[0] == 'needs_review'
    finally:
        pipeline.close(); analysis.close()


def test_changed_completed_paper_enters_queue_and_preserves_history(tmp_path):
    from test_paper_realtime import fixture_pipeline
    pipeline, analysis = fixture_pipeline(tmp_path)
    try:
        analysis.submit(['2610.10000'])
        with pipeline.db() as db:
            db.execute("UPDATE arxiv_paper_analyses SET status='complete',result_json='{}'")
        pipeline.tick()
        with pipeline.db() as db:
            assert db.execute('SELECT status FROM arxiv_paper_analyses').fetchone()[0] == 'complete'
            db.execute("UPDATE arxiv_papers SET metadata_json=json_set(metadata_json,'$.abstract','Revised experiment')")
        pipeline.tick()
        with pipeline.db() as db:
            assert db.execute('SELECT status FROM arxiv_paper_analyses').fetchone()[0] == 'queued'
            assert db.execute('SELECT count(*) FROM arxiv_paper_analysis_history').fetchone()[0] == 1
    finally:
        pipeline.close(); analysis.close()
