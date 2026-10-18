"""Risk views must not load intermediate history or unrelated workflow bodies."""
import copy,json,sqlite3
from unittest.mock import patch
from risk_analysis import _workflow_reports,_digest,read_risks
from risk_graph import _selected_workflows,load_risk_graph
from tests.test_risk_analysis import fixture


def database():
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE strategic_workflow_runs(id TEXT PRIMARY KEY,status TEXT,error TEXT,updated_at TEXT,snapshot_json TEXT);
      CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT,PRIMARY KEY(run_id,stage));
      CREATE TABLE risk_review_resolutions(risk_id TEXT,status TEXT,reason TEXT,corrected_source_url TEXT,replacement_workflow_run_id TEXT,created_at TEXT);''')
    for run in ('old','new','unassessed'):
        db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?)',(run,'complete','','2026-10-19','PRIVATE SNAPSHOT'))
    for run in ('old','new'):
        payload=fixture();payload['risk_report']['risks'][0]['title']=run+' risk'
        payload['risk_verification']['report_hash']=_digest(payload['risk_report'])
        db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(run,'final',json.dumps(payload)))
        db.execute('INSERT INTO strategic_workflow_artifacts VALUES (?,?,?)',(run,'model_call','PRIVATE INTERMEDIATE'))
    db.commit();return db


def test_stream_keeps_pending_coverage_without_reading_intermediate_or_snapshot_columns():
    db=database();reads=[]
    def authorize(action,table,column,*args):
        if action==sqlite3.SQLITE_READ:reads.append((table,column))
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorize)
    try:
        reports=list(_workflow_reports(db));assert len(reports)==3
        assert next(p for row,p in reports if row['id']=='unassessed') is None
        assert ('strategic_workflow_runs','snapshot_json') not in reads
        result=read_risks(db);assert result['coverage']['unassessed']==1
        assert len(result['risks'])==2
        assert 'PRIVATE' not in json.dumps(result)
    finally:db.close()


def test_graph_decodes_only_selected_final_and_keeps_status_gate():
    db=database();decoded=[]
    from graph_rag import _json
    def decode(body):decoded.append(body);return _json(body)
    try:
        with patch('risk_graph._json',side_effect=decode):
            assert list(_selected_workflows(db,{'new'}))
        assert len(decoded)==1 and 'new risk' in decoded[0] and 'PRIVATE' not in decoded[0]
        db.execute("UPDATE strategic_workflow_runs SET status='needs_review' WHERE id='new'")
        assert list(_selected_workflows(db,{'new'}))==[]
    finally:db.close()


def test_graph_query_skips_unrelated_replacement_check_but_keeps_history():
    db=database()
    try:
        payload=json.loads(db.execute("SELECT payload_json FROM strategic_workflow_artifacts WHERE run_id='old' AND stage='final'").fetchone()[0])
        identity='risk:'+_digest(['old',payload['risk_report']['risks'][0]])[:24]
        db.execute('INSERT INTO risk_review_resolutions VALUES (?,?,?,?,?,?)',(identity,'withdrawn_analysis_error','Interpretation corrected','https://example.com/a','new','2026-10-19'));db.commit()
        with patch('risk_views._replacement_verified',side_effect=AssertionError('Unrelated check')):
            graph=load_risk_graph(db,{'q':'new risk'})
            assert graph['coverage']['risks']==1
        with patch('risk_views._replacement_verified',return_value=True):
            history=load_risk_graph(db,{'q':'old risk','status':'withdrawn'})
            assert identity in {n['id'] for n in history['nodes']}
    finally:db.close()
