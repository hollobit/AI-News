import json
import sqlite3
from test_completion_quality import fixture
from workflow_retrieval import retrieve
from graph_quick_sources import setup_source_search


def store(path):
    run,item=fixture()
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE news(chat_id,message_id,channel);
        CREATE TABLE articles(chat_id,message_id,item_index,title,excerpt,text,day,topic,source_url,kind);
        CREATE TABLE rsi_cycles(id,created_at);
        CREATE TABLE corpus_completion_documents(cycle_id,document_id,status,workflow_run_id);
        CREATE TABLE strategic_workflow_runs(id,status,error);
        CREATE TABLE strategic_workflow_artifacts(run_id,stage,payload_json);
        CREATE TABLE source_excerpts(canonical_url,result_json,updated_at);
        INSERT INTO news VALUES(1,1,'test');
        INSERT INTO rsi_cycles VALUES('c','now');
        INSERT INTO strategic_workflow_runs VALUES('sample','complete','');''')
        db.execute('INSERT INTO articles VALUES(1,1,0,?,?,?,?,?,?,?)',(item['title'],'','','2026-10-03','tech',item['source_url'],'news'))
        db.execute("INSERT INTO corpus_completion_documents VALUES('c',?,'complete','sample')",(item['source_url'],))
        db.execute("INSERT INTO strategic_workflow_artifacts VALUES('sample','final',?)",(json.dumps(run['results'],ensure_ascii=False),))
        db.execute('INSERT INTO source_excerpts VALUES(?,?,?)',(item['source_url'],json.dumps(item['source_context'],ensure_ascii=False),'now'))
    setup_source_search(path)
    return run,item


def test_current_reviewed_graph_and_raw_observation_are_distinct(tmp_path):
    path=tmp_path/'db';store(path)
    result=retrieve(path,'AI 배포 중단 제안')
    assert result['retrieval']['validated_workflows']==1
    assert result['nodes']
    assert all('current_item' not in e for e in result['evidence'])
    assert result['retrieval']['total_ms']>=result['retrieval']['source_lookup_ms']


def test_changed_source_or_verdict_cannot_reuse_cached_claim(tmp_path):
    path=tmp_path/'db';run,item=store(path)
    assert retrieve(path,'AI 배포 중단 제안')['nodes']
    with sqlite3.connect(path) as db:
        db.execute("UPDATE source_excerpts SET result_json=?",(json.dumps({'status':'fetched','text':'변경된 원문'}),))
    assert not retrieve(path,'AI 배포 중단 제안')['nodes']
    with sqlite3.connect(path) as db:
        db.execute('UPDATE source_excerpts SET result_json=?',(json.dumps(item['source_context'],ensure_ascii=False),))
        db.execute("UPDATE strategic_workflow_runs SET status='needs_review'")
    assert not retrieve(path,'AI 배포 중단 제안')['nodes']


def test_changed_news_or_deleted_candidate_not_promoted(tmp_path):
    path=tmp_path/'db';store(path)
    assert retrieve(path,'AI 배포 중단 제안')['nodes']
    with sqlite3.connect(path) as db:db.execute("UPDATE articles SET title='AI 배포 중단 제안 철회'")
    assert not retrieve(path,'AI 배포 중단 제안')['nodes']
    with sqlite3.connect(path) as db:db.execute('DELETE FROM articles')
    assert retrieve(path,'AI 배포 중단 제안')['no_hits']


def test_selected_validation_is_shared_without_ignoring_changes(tmp_path):
    from unittest.mock import patch
    from workflow_retrieval import _workflow_graph
    path=tmp_path/'db';store(path)
    with patch('workflow_retrieval._workflow_graph',wraps=_workflow_graph) as project:
        first=retrieve(path,'AI 배포 중단 제안')
        second=retrieve(path,'AI 배포 중단 제안')
        assert first['nodes']==second['nodes']
        assert project.call_count==1
        with sqlite3.connect(path) as db:db.execute("UPDATE source_excerpts SET result_json='{}'")
        assert not retrieve(path,'AI 배포 중단 제안')['nodes']


def test_withdrawal_excludes_cached_interpretation(tmp_path):
    from test_risk_analysis import fixture as risk_fixture
    from strategic_workflow import digest
    path=tmp_path/'db';run,item=store(path)
    payload=run['results'];risk=risk_fixture()['risk_report']['risks'][0]
    risk['evidence_ids']=['news_a']
    payload['risk_report'].update(risks=[risk],assessed_evidence_ids=['news_a'],not_assessable_evidence_ids=['url_a'])
    payload['risk_verification']['report_hash']=digest(payload['risk_report'])
    with sqlite3.connect(path) as db:
        db.execute('UPDATE strategic_workflow_artifacts SET payload_json=?',(json.dumps(payload,ensure_ascii=False),))
        db.execute('CREATE TABLE risk_review_resolutions(risk_id,status,reason,corrected_source_url,replacement_workflow_run_id,created_at)')
    assert retrieve(path,'AI 배포 중단 제안')['nodes']
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO risk_review_resolutions VALUES(?,?,?,?,?,?)',('risk:'+digest(['sample',risk])[:24],'withdrawn_source_mismatch','wrong source','https://example.com/corrected','','now'))
    assert not retrieve(path,'AI 배포 중단 제안')['nodes']


def test_parallel_requests_share_selected_validation(tmp_path):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    from workflow_retrieval import _workflow_graph
    path=tmp_path/'db';store(path)
    def project(*args):
        time.sleep(.03)
        return _workflow_graph(*args)
    with patch('workflow_retrieval._workflow_graph',side_effect=project) as check:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:retrieve(path,'AI 배포 중단 제안'),range(4)))
        assert all(r['nodes'] for r in results)
        assert check.call_count==1


def test_candidate_changed_between_lookup_and_snapshot_is_excluded(tmp_path):
    from unittest.mock import patch
    from graph_quick_sources import cold_answer
    path=tmp_path/'db';store(path)
    def changing(*args,**kwargs):
        found=cold_answer(*args,**kwargs)
        with sqlite3.connect(path) as db:db.execute("UPDATE articles SET title='바뀐 기사'")
        return found
    with patch('workflow_retrieval.cold_answer',side_effect=changing):
        result=retrieve(path,'AI 배포 중단 제안')
    assert result['no_hits'] and not result['nodes']
