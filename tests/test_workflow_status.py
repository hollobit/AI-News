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


def test_task_timeline_parallel_retry_and_restarted_stage(tmp_path):
    from server_routes.workflow_tasks import task_detail
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE strategic_workflow_events(seq INTEGER PRIMARY KEY,run_id,stage,status,created_at);
    CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json);
    CREATE TABLE completion_engine_waits(round_id,failures,next_attempt_at);''')
    db.execute("INSERT INTO strategic_workflow_artifacts VALUES('w','execution_plan','{\"path\":\"parallel-drafts-v1\"}')")
    now=datetime.now(timezone.utc);stamp=now.timestamp();when=now.isoformat()
    for stage,status in [('collection','complete'),('strategy_draft','complete'),('strategy_draft','running'),('risk_assessment','running'),('model_call_secret','complete')]:
        db.execute('INSERT INTO strategic_workflow_events(run_id,stage,status,created_at) VALUES(?,?,?,?)',('w',stage,status,when))
    db.execute("INSERT INTO completion_engine_waits VALUES('r',2,?)",(stamp+60,))
    task=dict(id='r',workflow_run_id='w',status='running',created_at=when,completed_at=None)
    result=task_detail(db,task,{'strategic_workflow_artifacts','completion_engine_waits'},stamp,True)
    assert result['step_complete']==1 and result['step_total']==7
    assert result['step_percent']==14
    assert len([s for s in result['steps'] if s['status']=='retry'])==2
    assert not any(s['stage']=='model_call_secret' for s in result['steps'])
    result=task_detail(db,task,{'strategic_workflow_artifacts'},stamp,False)
    assert len([s for s in result['steps'] if s['status']=='owner_missing'])==2
    result=task_detail(db,task,set(),stamp,True)
    assert result['step_percent'] is None
    db.close()
