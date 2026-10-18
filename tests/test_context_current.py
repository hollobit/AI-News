import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from context_current import current,dependencies,load
from context_index import write
from tests import test_bulk_baseline as fixture


def test_source_bound_admission_accepts_unrelated_change_but_rejects_edit_and_withdrawal():
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:2]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with service.db() as db:
            wanted=db.execute('SELECT input_hash FROM bulk_baseline_cache ORDER BY document_id LIMIT 1').fetchone()[0]
        key=json.dumps(['baseline',wanted,None],ensure_ascii=False,separators=(',',':'))
        with patch('improvement_selection.all_corpus_items',side_effect=lambda db:case.items):
            with service.db() as db:expected=dependencies(load(db,[key]))
            assert expected and current(case.path,expected)
            case.items[1]['text']='unrelated changed input'
            assert current(case.path,expected)
            case.items[0]['text']='the cited input was edited'
            assert not current(case.path,expected)
            case.items[0]['text']='정부 소버린 AI 인프라 사업'
            assert current(case.path,expected)
            with service.db() as db:db.execute('DELETE FROM bulk_baseline_cache WHERE input_hash=?',(wanted,))
            assert not current(case.path,expected)
    finally:case.tearDown()


def test_current_source_passage_hash_must_match():
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:1]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with patch('improvement_selection.all_corpus_items',return_value=case.items):
            with service.db() as db:
                h=db.execute('SELECT input_hash FROM bulk_baseline_cache').fetchone()[0]
                expected=dependencies(load(db,[json.dumps(['baseline',h,None],separators=(',',':'))]))
                db.execute('CREATE TABLE source_health(url TEXT,hash TEXT,last_status TEXT)')
                db.execute("INSERT INTO source_health VALUES('https://example.org','original','fetched')")
            passages=[{'url':'https://example.org','hash':'original'}]
            assert current(case.path,expected,passages)
            with service.db() as db:db.execute("UPDATE source_health SET hash='new qualifier outside the short excerpt'")
            assert not current(case.path,expected,passages)
    finally:case.tearDown()


def test_consistent_workspace_requires_recent_current_policy_and_refreshes_records(tmp_path):
    from graph_snapshot import GraphSnapshots
    from context_index import read,materialize
    case=fixture.BaselineTests();case.setUp();case.items=case.items[:1]
    snapshots=None
    try:
        service=case.service();case.done(service,service.start()['id'])
        snapshots=GraphSnapshots(case.path,directory=tmp_path/'indexes')
        file=snapshots.directory/'snapshot.sqlite3'
        with patch('improvement_selection.all_corpus_items',return_value=case.items):
            with service.db() as db:write(file,db,'old')
            with sqlite3.connect(file) as db:db.execute("UPDATE meta SET body='0' WHERE key='servable'")
            graph=snapshots.available_workspace()
            assert graph is not None
            selected=materialize(graph,'소버린 AI')
            assert selected.full_evidence and selected.workspace_dependencies
            case.items[0]['text']='source changed'
            selected=materialize(graph,'소버린 AI')
            assert not selected.full_evidence
            with sqlite3.connect(file) as db:db.execute("UPDATE meta SET body='0' WHERE key='admitted_at'")
            assert snapshots.available_workspace() is None
    finally:
        if snapshots:snapshots.close()
        case.tearDown()
