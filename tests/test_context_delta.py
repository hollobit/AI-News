import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from context_delta import changed_keys,position
from context_index import write,read,materialize


def test_only_baseline_publications_can_use_delta(tmp_path):
    path=tmp_path/'source.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE articles(id INTEGER PRIMARY KEY,text TEXT);CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY,result_json TEXT,prepared_json TEXT);')
        first=position(db)
        db.execute("INSERT INTO bulk_baseline_cache VALUES('a','{}','{}')");db.commit()
        second=position(db)
        assert changed_keys(db,first,second)=={'a'}
        db.execute("UPDATE bulk_baseline_cache SET input_hash='b'");db.commit()
        assert changed_keys(db,second,position(db))=={'a','b'}
        db.execute("INSERT INTO articles VALUES(1,'new source')");db.commit()
        assert changed_keys(db,second,position(db)) is None
        current=position(db)
        db.execute('DELETE FROM baseline_search_changes');db.commit()
        assert changed_keys(db,first,position(db)) is None
        assert changed_keys(db,current,None) is None


def test_baseline_delta_matches_full_admission_and_preserves_other_sources(tmp_path):
    from tests.test_bulk_baseline import BaselineTests
    case=BaselineTests();case.setUp();all_items=case.items[:2];case.items=all_items[:1]
    try:
        service=case.service();case.done(service,service.start()['id'])
        with patch('improvement_selection.all_corpus_items',return_value=all_items):
            first=tmp_path/'first.sqlite3';delta=tmp_path/'delta.sqlite3';full=tmp_path/'full.sqlite3'
            with service.db() as db:write(first,db,'first')
            case.items=all_items;case.done(service,service.start()['id'])
            with service.db() as db,patch('graph_rag._analysis_sources',side_effect=AssertionError('full scan')):
                result=write(delta,db,'current',first)
            assert result['mode']=='baseline_delta' and result['changed']==1 and result['sources']==2
            with service.db() as db:write(full,db,'current')
            assert materialize(read(delta,'current'),'소버린').full_evidence==materialize(read(full,'current'),'소버린').full_evidence
            with service.db() as db:
                db.execute("UPDATE bulk_baseline_cache SET result_json='{}' WHERE document_id='https://example.com/0'")
            removed=tmp_path/'removed.sqlite3'
            with service.db() as db:
                result=write(removed,db,'removed',delta)
            assert result['mode']=='baseline_delta' and result['removed']==1 and result['sources']==1
    finally:case.tearDown()


def test_delta_watermark_is_bound_to_consistent_read_snapshot(tmp_path):
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY,result_json TEXT,prepared_json TEXT)')
        db.commit();before=position(db)
        db.execute('BEGIN');assert position(db)==before
        with sqlite3.connect(path) as writer:
            writer.execute("INSERT INTO bulk_baseline_cache VALUES('new','{}','{}')")
        assert position(db)==before
        db.rollback()
        assert changed_keys(db,before,position(db))=={'new'}
