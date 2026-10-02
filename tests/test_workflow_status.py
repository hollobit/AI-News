import sqlite3
from datetime import datetime, timezone
from server_routes.workflow_status import snapshot


def test_live_flow_separates_denominators_and_detects_orphan(tmp_path):
    path=tmp_path/'news.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE state(key,value);
        CREATE TABLE bulk_baseline_runs(id,status,owner_pid,updated_at,error,created_at);
        CREATE TABLE bulk_baseline_documents(run_id,status);
        CREATE TABLE rsi_cycles(id,status,owner_pid,updated_at,error,created_at);
        CREATE TABLE corpus_completion_documents(cycle_id,status);
        CREATE TABLE rsi_rounds(cycle_id,number,status,created_at,completed_at);
        CREATE TABLE arxiv_paper_analyses(status);''')
        db.execute('INSERT INTO state VALUES(?,?)',('collector_last_success',datetime.now(timezone.utc).isoformat()))
        db.execute("INSERT INTO state VALUES('collector_corpus_snapshot','{\"status\":\"complete\",\"total_unique\":999}')")
        db.execute("INSERT INTO bulk_baseline_runs VALUES('b','requires_review',NULL,'now','','now')")
        db.executemany("INSERT INTO bulk_baseline_documents VALUES('b',?)", [('verified',)]*1000+[('needs_review',)])
        db.execute("INSERT INTO rsi_cycles VALUES('d','running',NULL,'now','','now')")
        db.executemany("INSERT INTO corpus_completion_documents VALUES('d',?)", [('complete',)]*3+[('pending',),('running',)])
        db.execute("INSERT INTO arxiv_paper_analyses VALUES('complete')")
    before=path.stat().st_mtime_ns
    data=snapshot(path);stages={s['id']:s for s in data['stages']}
    assert stages['collector']['status']=='live'
    assert stages['extract']['observed']==999 and stages['extract']['percent'] is None
    assert stages['baseline']['percent']==99.9
    assert stages['deep']['percent']==60 and stages['deep']['total']==5
    assert stages['deep']['status']=='owner_missing'
    assert stages['papers']['total']==1
    assert data['calls']==[] and not data['runtime_error']
    assert path.stat().st_mtime_ns==before


def test_empty_store_has_unknown_not_fake_complete(tmp_path):
    path=tmp_path/'news.sqlite3'
    sqlite3.connect(path).close()
    result=snapshot(path)
    assert len(result['stages'])==8
    assert all(s['percent'] is None for s in result['stages'])
    assert result['stages'][0]['status']=='delayed'
