import sqlite3
import pytest
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
    assert len(result['stages'])==10
    assert all(s['percent'] is None for s in result['stages'])
    assert result['stages'][0]['status']=='delayed'


@pytest.mark.parametrize('state,healthy,status',[('coalescing',True,'queued'),('publishing',True,'running'),('retry_wait',True,'waiting'),('watching',True,'recorded'),('publishing',False,'owner_missing')])
def test_publisher_progress_never_claims_remote_deployment(tmp_path,state,healthy,status):
    import json,os,time
    directory=tmp_path/'data';directory.mkdir();path=directory/'news.sqlite3';sqlite3.connect(path).close()
    runtime=tmp_path/'.runtime';runtime.mkdir()
    (runtime/'public-site.publisher.json').write_text(json.dumps(dict(state=state,pid=os.getpid(),checked_at=time.time()-(0 if healthy else 30),remaining_seconds=7)))
    (runtime/'public-site.publication-result.json').write_text(json.dumps(dict(commit='a'*40,submitted_at=100,checked_at=200)))
    stage=next(s for s in snapshot(path)['stages'] if s['id']=='publication')
    assert stage['status']==status and stage['percent'] is None
    assert stage['publication']['commit']=='a'*12
    assert stage['publisher']['state']==(state if healthy else 'unavailable')
    assert 'checked_at' not in stage['publisher']
    if state=='coalescing':assert '7초' in stage['note'] and '별도' in stage['note']


@pytest.mark.parametrize('health,state', [('observed','coalescing'), ('unresponsive','coalescing'), ('observed','disabled')])
def test_wiki_input_wait_is_not_analysis_completion(tmp_path, monkeypatch, health, state):
    import background_jobs
    path=tmp_path/'news.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE wiki_topics(status)')
        db.execute("INSERT INTO wiki_topics VALUES('complete')")
    monkeypatch.setattr(background_jobs,'worker_status',lambda _:dict(status='running',health=health,
        wiki_source_updates=dict(state=state,remaining_seconds=7,checked_at=123,notifications=2)))
    stage=next(s for s in snapshot(path)['stages'] if s['id']=='wiki')
    assert stage['complete']==1 and stage['status']=='recorded'
    assert 'checked_at' not in stage['source_updates']
    if health=='unresponsive':
        assert stage['source_updates']['state']=='unavailable' and '확인할 수 없습니다' in stage['note']
    elif state=='coalescing':
        assert '7초 후 갱신을 확인' in stage['note'] and '완료 예상 시간은 아닙니다' in stage['note']
    else:assert '중지' in stage['note']


@pytest.mark.parametrize('state',['live','stale','orphan'])
def test_source_coalescing_is_visible_without_faking_a_running_analysis(tmp_path,state):
    import json,os
    from datetime import timedelta
    directory=tmp_path/'data';directory.mkdir();path=directory/'news.sqlite3'
    sqlite3.connect(path).close()
    runtime=tmp_path/'.runtime';runtime.mkdir()
    stamp=datetime.now(timezone.utc)-timedelta(seconds=20 if state=='stale' else 0)
    (runtime/'scheduled-collection.json').write_text(json.dumps({
        'stage':'waiting_for_sources','checked_at':stamp.isoformat(),'source_coalescing':{
            'documents':17,'ready_documents':8,'remaining_seconds':20,'owner_pid':None if state=='orphan' else os.getpid()}}))
    baseline=next(s for s in snapshot(path)['stages'] if s['id']=='baseline')
    assert baseline['status']=='unknown' and baseline['percent'] is None
    if state=='live':
        assert baseline['source_coalescing']['documents']==17
        assert '8/17건 처리' in baseline['note']
    else:assert 'source_coalescing' not in baseline


@pytest.mark.parametrize('legacy',[False,True])
def test_flow_reports_requested_speed_without_assuming_provider_delivery(tmp_path,legacy):
    import os,time
    from llm_runtime import LLMRuntime
    path=tmp_path/'news.sqlite3';sqlite3.connect(path).close()
    runtime=LLMRuntime(tmp_path/'llm_runtime.sqlite3')
    with runtime.db() as db:
        if legacy:db.execute('ALTER TABLE llm_calls DROP COLUMN service_tier_requested')
        db.execute("INSERT INTO llm_calls(role,status,owner_pid,queued_at,input_chars,schema_chars,model) VALUES('strategic_question_verification','queued',?,?,0,0,'gpt-6.1-sol')",(os.getpid(),time.time()))
        if not legacy:db.execute("UPDATE llm_calls SET service_tier_requested='fast'")
    result=snapshot(path)
    assert not result['runtime_error'] and len(result['calls'])==1
    assert result['calls'][0]['service_tier_requested']==('' if legacy else 'fast')
    assert 'service_tier_actual' not in result['calls'][0]


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


def test_source_synthesis_timeline_shows_four_active_stages_and_pending_review():
    import json
    from server_routes.workflow_tasks import task_detail
    from workflow_synthesis import VERSION
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE strategic_workflow_events(seq INTEGER PRIMARY KEY,run_id,stage,status,created_at);
    CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json);''')
    db.execute('INSERT INTO strategic_workflow_artifacts VALUES(?,?,?)',
               ('w','execution_plan',json.dumps(dict(path='multi-role',synthesis_schedule=VERSION))))
    when=datetime.now(timezone.utc)
    active={'national','technology','risk_assessment','synthesis'}
    for stage in ('collection','enrichment','execution_plan','morphology','graph_retrieval',*active):
        db.execute('INSERT INTO strategic_workflow_events(run_id,stage,status,created_at) VALUES(?,?,?,?)',
                   ('w',stage,'running' if stage in active else 'complete',when.isoformat()))
    task=dict(id='r',workflow_run_id='w',status='running',created_at=when.isoformat(),completed_at=None)
    result=task_detail(db,task,{'strategic_workflow_artifacts'},when.timestamp(),True)
    assert {s['stage'] for s in result['steps'] if s['status']=='running'}==active
    assert result['step_complete']==5 and result['step_total']==13
    assert all(s['status']=='pending' for s in result['steps'] if s['stage'] in ('verification','risk_verification','final'))
    db.close()
