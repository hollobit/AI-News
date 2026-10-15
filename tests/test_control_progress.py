import json
import os
import sqlite3

from server_routes.decision_control import overview, detail
from server_routes.control_progress import current


def test_missing_stage_history_does_not_invent_a_percentage():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE strategic_workflow_events(seq,run_id,stage,status,created_at);
        CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json);
        INSERT INTO strategic_workflow_artifacts VALUES('old','execution_plan','{"path":"compact-v1"}');''')
    result=current(db,{'strategic_workflow_events','strategic_workflow_artifacts'},'workflow','old','complete')
    assert result['percent'] is None and result['total'] is None
    db.close()


def test_progress_tracks_parallel_stages_retries_and_dead_owners(tmp_path):
    path=tmp_path/'news.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE strategic_workflow_runs(id,status,created_at,updated_at,request_json,snapshot_json);
          CREATE TABLE strategic_workflow_events(seq INTEGER PRIMARY KEY,run_id,stage,status,created_at);
          CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json);''')
        db.execute('INSERT INTO strategic_workflow_runs VALUES(?,?,?,?,?,?)',('w','running','2026-10-15T01:00:00Z','2026-10-15T01:00:00Z',json.dumps({'owner_pid':os.getpid()}),'[]'))
        db.execute("INSERT INTO strategic_workflow_artifacts VALUES('w','execution_plan','{\"path\":\"parallel-drafts-v1\"}')")
        for stage,state in [('collection','complete'),('enrichment','complete'),('execution_plan','complete'),('strategy_draft','running'),('risk_assessment','running')]:
            db.execute("INSERT INTO strategic_workflow_events(run_id,stage,status,created_at) VALUES('w',?,?,?)",(stage,state,'2026-10-15T01:00:01Z'))
    first=overview(path)['jobs'][0]['progress']
    assert first['completed']==3 and first['total']==7 and len(first['active'])==2
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO strategic_workflow_events(run_id,stage,status,created_at) VALUES('w','strategy_draft','complete','2026-10-15T01:00:02Z')")
    second=overview(path)['jobs'][0]['progress']
    assert second['revision']!=first['revision'] and second['completed']==4
    assert [s['stage'] for s in second['active']]==['risk_assessment']
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO strategic_workflow_events(run_id,stage,status,created_at) VALUES('w','strategy_draft','running','2026-10-15T01:00:03Z')")
    assert overview(path)['jobs'][0]['progress']['completed']==3
    with sqlite3.connect(path) as db:db.execute("UPDATE strategic_workflow_runs SET request_json='{}'")
    before=path.stat().st_mtime_ns
    result=detail(path,'workflow','w')['job']
    assert result['status']=='owner_missing' and not result['progress']['active']
    assert path.stat().st_mtime_ns==before


def test_current_question_progress_uses_reset_body_not_old_completed_events():
    from question_progress import setup,update,STAGES
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;setup(db)
    for s in STAGES:update(db,'q',s,done=True)
    ready=current(db,{'graph_question_progress'},'question','q','needs_review')
    assert ready['percent']<100
    update(db,'q','evidence',reset=True)
    resumed=current(db,{'graph_question_progress'},'question','q','running')
    assert resumed['completed']==0 and resumed['total']==5
    assert [s['stage'] for s in resumed['active']]==['evidence']
    assert resumed['revision']!=ready['revision']
    db.close()
