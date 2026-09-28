import json,sqlite3,unittest
from pathlib import Path
from status_views import status_response,status_version
class StatusViewsTest(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:')
  self.db.executescript('''CREATE TABLE strategic_workflow_runs(id,status,created_at,updated_at,error,snapshot_json);
  CREATE TABLE strategic_workflow_events(seq,run_id,stage,status,created_at,detail);
  CREATE TABLE rsi_cycles(id,status,created_at,updated_at,error,next_run_at,pause_requested,no_progress,coverage_json,state_json,seen_json);
  CREATE TABLE rsi_rounds(id,cycle_id,number,status,created_at,completed_at,error,workflow_run_id);''')
 def tearDown(self):self.db.close()
 def test_workflow_omits_payload_and_etag_changes_with_stage(self):
  self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?,?)',('w','running','1','1','',json.dumps([{'text':'x'*1000000}])))
  self.db.execute('INSERT INTO strategic_workflow_events VALUES (1,?,?,?,?,?)',('w','national','running','1','x'*2000))
  result=status_response(self.db,'workflows','w')
  self.assertEqual(result['snapshot_count'],1);self.assertLess(len(json.dumps(result)),2000);self.assertNotIn('snapshot',result)
  previous=result['version'];self.db.execute('INSERT INTO strategic_workflow_events VALUES (2,?,?,?,?,?)',('w','national','complete','2','done'))
  self.assertNotEqual(previous,status_response(self.db,'workflows','w')['version'])
 def test_improvement_aggregates_only_current_scope(self):
  coverage={'total_unique':2,'duplicates_excluded':5,'corpus_ids':['a','b']};state={'a':{'status':'verified','risk_assessed':True},'outside':{'status':'verified'}}
  self.db.execute('INSERT INTO rsi_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)',('r','running','1','1','',None,0,0,json.dumps(coverage),json.dumps(state),'["a"]'))
  self.db.execute('INSERT INTO rsi_rounds VALUES (?,?,?,?,?,?,?,?)',('round','r',1,'running','1',None,'','w'))
  result=status_response(self.db,'improvement','r');self.assertEqual(result['metrics']['processed_unique'],1);self.assertEqual(result['metrics']['remaining'],1);self.assertFalse(result['metrics']['all_verified']);self.assertEqual(result['current_workflow_id'],'w');self.assertNotIn('rounds',result)
 def test_empty_and_missing(self):
  self.assertEqual(status_response(self.db,'baseline')['runs'],[]);self.assertIsNone(status_response(self.db,'workflows','missing'))
  self.assertEqual(status_version({'a':1}),status_version({'a':1,'version':'old'}))
 def test_parallel_completion_ledger_and_risk_dispositions(self):
  coverage={'total_unique':4,'corpus_ids':['a','b','c','d']}
  state={'a':{'status':'verified','risk_assessed':True,'risk_reviewed':True},'b':{'status':'verified','risk_assessed':False,'risk_reviewed':True},'c':{'status':'needs_review','risk_reviewed':False},'outside':{'status':'verified','risk_reviewed':True}}
  self.db.execute('INSERT INTO rsi_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)',('r','running','1','1','',None,0,0,json.dumps(coverage),json.dumps(state),'[]'))
  for i in range(9):
   identity='w'+str(i)
   self.db.execute('INSERT INTO strategic_workflow_runs VALUES (?,?,?,?,?,?)',(identity,'complete' if i==7 else 'running','1','1','','[]'))
   self.db.execute('INSERT INTO rsi_rounds VALUES (?,?,?,?,?,?,?,?)',('round'+str(i),'other' if i==6 else 'r',i,'complete' if i==8 else 'running','1',None,'',identity))
  self.db.execute('INSERT INTO rsi_rounds VALUES (?,?,?,?,?,?,?,?)',('duplicate','r',10,'running','1',None,'','w0'))
  self.db.execute('CREATE TABLE corpus_completion_documents(cycle_id,document_id,status)')
  self.db.executemany('INSERT INTO corpus_completion_documents VALUES (?,?,?)',[('r','a','complete'),('r','b','needs_review'),('r','c','running'),('r','d','pending'),('other','x','failed')])
  result=status_response(self.db,'improvement','r');m=result['metrics']
  self.assertEqual(m['active_workflows'],6)
  self.assertEqual(m['risk_assessed_unique'],1)
  self.assertEqual(m['risk_reviewed_unique'],2)
  self.assertEqual(m['risk_information_insufficient'],1)
  self.assertEqual(m['completion_total'],4)
  self.assertEqual(m['completion_complete'],1)
  self.assertEqual(m['completion_failed'],0)
  self.assertLess(len(json.dumps(result)),4000)
  self.db.execute("UPDATE corpus_completion_documents SET status='failed' WHERE cycle_id='r' AND document_id='c'")
  self.assertNotEqual(result['version'],status_response(self.db,'improvement','r')['version'])
 def test_old_cycle_without_ledger_remains_compatible(self):
  self.db.execute('INSERT INTO rsi_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)',('old','paused','1','1','',None,0,0,'{"total_unique":1}','{"x":{"status":"verified","risk_assessed":true}}','[]'))
  result=status_response(self.db,'improvement','old')['metrics']
  self.assertEqual(result['risk_assessed_unique'],1)
  self.assertEqual(result['risk_reviewed_unique'],1)
  self.assertEqual(result['risk_information_insufficient'],0)
  self.assertNotIn('completion_total',result)
  self.assertEqual(result['active_workflows'],0)
 def test_legacy_reviewed_fallback_preserves_explicit_false_and_null(self):
  state={'legacy':{'risk_assessed':True},'explicit_false':{'risk_assessed':True,'risk_reviewed':False},'explicit_null':{'risk_assessed':True,'risk_reviewed':None},'insufficient':{'risk_assessed':False,'risk_reviewed':True},'empty':{}}
  coverage={'total_unique':len(state),'corpus_ids':list(state)}
  self.db.execute('INSERT INTO rsi_cycles VALUES (?,?,?,?,?,?,?,?,?,?,?)',('r','paused','1','1','',None,0,0,json.dumps(coverage),json.dumps(state),'[]'))
  m=status_response(self.db,'improvement','r')['metrics']
  self.assertEqual(m['risk_assessed_unique'],3)
  self.assertEqual(m['risk_reviewed_unique'],2)
  self.assertEqual(m['risk_information_insufficient'],1)
if __name__=='__main__':unittest.main()
