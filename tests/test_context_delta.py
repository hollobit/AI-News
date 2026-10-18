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


def test_paper_delta_matches_full_admission_after_publication_revocation_and_metadata_edit(tmp_path):
    from tests.test_paper_graph import PaperGraphTests
    case=PaperGraphTests();case.setUp()
    path=tmp_path/'news.db'
    case.db.close();case.db=sqlite3.connect(path)
    from arxiv_papers import init_papers
    init_papers(case.db)
    case.db.executescript('''CREATE TABLE arxiv_paper_analyses(paper_id TEXT PRIMARY KEY,input_hash TEXT,status TEXT,result_json TEXT,error TEXT);
        CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY,result_json TEXT,prepared_json TEXT);''')
    corpus=patch('improvement_selection.all_corpus_items',return_value=[]);corpus.start()
    def contents(path):
        with sqlite3.connect(path) as store:
            rows=store.execute('SELECT source_key,body FROM sources ORDER BY source_key').fetchall()
            coverage=store.execute("SELECT body FROM meta WHERE key='coverage'").fetchone()[0]
            store.execute("INSERT INTO search(search,rank) VALUES('integrity-check',1)")
        return rows,json.loads(coverage)
    try:
        case.add('2609.10001');case.db.commit()
        previous=tmp_path/'first.sqlite3';write(previous,case.db,'first')
        actions=[lambda:case.add('2609.10002'),
                 lambda:case.db.execute("UPDATE arxiv_paper_analyses SET status='needs_review' WHERE paper_id='2609.10001'"),
                 lambda:case.db.execute("UPDATE arxiv_papers SET metadata_json=json_set(metadata_json,'$.abstract','Changed source') WHERE paper_id='2609.10002'"),
                 lambda:case.db.execute("DELETE FROM arxiv_paper_analyses WHERE paper_id='2609.10001'")]
        for i,action in enumerate(actions):
            action();case.db.commit()
            delta=tmp_path/f'delta{i}.sqlite3';full=tmp_path/f'full{i}.sqlite3'
            with patch('graph_rag._analysis_sources',side_effect=AssertionError('full archive scan')):
                result=write(delta,case.db,str(i),previous)
            assert result['mode']=='publication_delta'
            write(full,case.db,str(i))
            assert contents(delta)==contents(full)
            previous=delta
    finally:corpus.stop();case.tearDown()


def test_paper_delta_requires_complete_journal_and_unknown_changes_fall_back(tmp_path):
    from context_delta import publication_changes
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY);
            CREATE TABLE arxiv_papers(paper_id TEXT PRIMARY KEY);
            CREATE TABLE research_documents(id TEXT);''')
        first=position(db)
        db.execute("INSERT INTO arxiv_papers VALUES('one')")
        db.execute("INSERT INTO bulk_baseline_cache VALUES('base')");db.commit()
        assert publication_changes(db,first,position(db))==dict(baseline={'base'},paper={'one'},workflow=set())
        db.execute('DELETE FROM paper_search_changes');db.commit()
        assert publication_changes(db,first,position(db)) is None
        first=position(db)
        db.execute("INSERT INTO research_documents VALUES('unknown')");db.commit()
        assert publication_changes(db,first,position(db)) is None


def test_workflow_delta_preserves_risk_payload_and_removes_revoked_verdicts(tmp_path):
    from tests.test_graph_rag import IntegratedGraphTests
    from graph_rag import _analysis_sources
    case=IntegratedGraphTests();case.db=sqlite3.connect(tmp_path/'news.db')
    case.db.execute('CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY,result_json TEXT,prepared_json TEXT)')
    try:
        with patch('improvement_selection.all_corpus_items',return_value=[]):
            case.add_workflow('first');case.db.commit()
            previous=tmp_path/'first.sqlite3';write(previous,case.db,'first')
            actions=[lambda:case.add_workflow('second'),
                     lambda:case.db.execute("UPDATE strategic_workflow_runs SET error='revoked' WHERE id='first'"),
                     lambda:case.db.execute("UPDATE strategic_workflow_artifacts SET payload_json='{}' WHERE run_id='second'")]
            for i,action in enumerate(actions):
                action();case.db.commit()
                delta=tmp_path/f'wdelta{i}.sqlite3';full=tmp_path/f'wfull{i}.sqlite3'
                def selected(db,selected=None):
                    assert selected and selected.get('workflow')
                    return _analysis_sources(db,selected=selected)
                with patch('graph_rag._analysis_sources',side_effect=selected):
                    assert write(delta,case.db,str(i),previous)['mode']=='publication_delta'
                write(full,case.db,str(i))
                with sqlite3.connect(delta) as actual,sqlite3.connect(full) as expected:
                    for query in ('SELECT source_key,body,text FROM sources ORDER BY source_key',"SELECT body FROM meta WHERE key='coverage'"):
                        assert actual.execute(query).fetchall()==expected.execute(query).fetchall()
                    actual.execute("INSERT INTO search(search,rank) VALUES('integrity-check',1)")
                previous=delta
    finally:case.db.close()


def test_paper_checkpoint_writes_do_not_rebuild_search_but_public_changes_do(tmp_path):
    from context_delta import publication_changes
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY);
            CREATE TABLE arxiv_paper_analyses(paper_id TEXT PRIMARY KEY,input_hash TEXT,status TEXT,error TEXT,result_json TEXT,
                owner_pid INTEGER,updated_at TEXT,draft_json TEXT,evidence_json TEXT);
            INSERT INTO arxiv_paper_analyses VALUES('paper','hash','running','',NULL,123,'old',NULL,NULL);''')
        before=position(db)
        db.execute("UPDATE arxiv_paper_analyses SET draft_json='draft',evidence_json='evidence',owner_pid=234,updated_at='new'");db.commit()
        assert position(db)==before
        db.execute("UPDATE arxiv_paper_analyses SET result_json='verified result',status='complete'");db.commit()
        after=position(db)
        assert publication_changes(db,before,after)==dict(baseline=set(),paper={'paper'},workflow=set())
        assert after['analysis'][0]-before['analysis'][0]==1
        db.execute("UPDATE arxiv_paper_analyses SET error='withdrawn'");db.commit()
        assert publication_changes(db,after,position(db))['paper']=={'paper'}


def test_paper_checkpoint_trigger_upgrade_rotates_epoch_and_keeps_revision_journal_in_sync(tmp_path):
    from context_delta import publication_changes
    path=tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE bulk_baseline_cache(input_hash TEXT PRIMARY KEY);
            CREATE TABLE arxiv_paper_analyses(paper_id TEXT PRIMARY KEY,updated_at TEXT);
            INSERT INTO arxiv_paper_analyses VALUES('paper','old');''')
        original=position(db)
        db.execute("UPDATE arxiv_paper_analyses SET updated_at='checkpoint'");db.commit()
        legacy=position(db)
        assert publication_changes(db,original,legacy)['paper']=={'paper'}
        for column in ('input_hash','status','error','result_json'):
            db.execute(f'ALTER TABLE arxiv_paper_analyses ADD COLUMN {column} TEXT')
        db.commit()
        upgraded=position(db)
        assert publication_changes(db,legacy,upgraded) is None
        # A draining older process uses the same CREATE IF NOT EXISTS names;
        # it must not reinstall an additional unfiltered trigger.
        for name in ('paper_search_v1_arxiv_paper_analyses_update','projection_v1_arxiv_paper_analyses_update'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER UPDATE ON arxiv_paper_analyses
                BEGIN SELECT RAISE(ABORT,'old trigger should not be recreated'); END''')
        db.commit()
        db.execute("UPDATE arxiv_paper_analyses SET updated_at='next checkpoint'");db.commit()
        assert position(db)==upgraded
        db.execute("UPDATE arxiv_paper_analyses SET input_hash='changed'");db.commit()
        changed=position(db)
        assert changed['analysis'][0]-upgraded['analysis'][0]==1
        assert publication_changes(db,upgraded,changed)['paper']=={'paper'}
