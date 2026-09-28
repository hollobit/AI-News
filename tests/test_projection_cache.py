import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import projection_cache as cache
from graph_rag import load_integrated_graph


def database(path):
    db=sqlite3.connect(path); db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY,text TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT,updated_at TEXT)')
    db.commit()
    return db


def test_schema_setup_does_not_hold_cache_lock_during_database_wait(tmp_path):
    path=tmp_path/'lock.sqlite'
    database(path).close()
    entered=threading.Event();release=threading.Event()
    class WaitingConnection(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql.startswith('CREATE TABLE IF NOT EXISTS projection_revisions'):
                entered.set()
                assert release.wait(3)
            return super().execute(sql,*args,**kwargs)
    def setup():
        with sqlite3.connect(path,factory=WaitingConnection) as db:
            cache.revision_token(db)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(setup)
        try:
            assert entered.wait(2)
            acquired=cache._LOCK.acquire(timeout=.2)
            if acquired:cache._LOCK.release()
            assert acquired,'SQLite waits must not prevent other threads using the cache'
        finally:release.set()
        future.result(timeout=3)


def test_revisions_meaning_and_database_isolation(tmp_path):
    a=database(tmp_path/'a.sqlite'); b=database(tmp_path/'b.sqlite')
    before=cache.revision_token(a,('source',))
    assert before!=cache.revision_token(b,('source',))
    a.execute("INSERT INTO articles VALUES(1,'first')"); a.commit()
    assert cache.revision_token(a,('source',))!=before
    payload={'status':'fetched','title':'제목','text':'본문','fetched_at':'one'}
    a.execute('INSERT INTO source_excerpts VALUES(?,?,?)',('https://example.org',json.dumps(payload),'one')); a.commit()
    before=cache.revision_token(a,('source',))
    payload['fetched_at']='two'
    a.execute('INSERT OR REPLACE INTO source_excerpts VALUES(?,?,?)',('https://example.org',json.dumps(payload),'two')); a.commit()
    assert cache.revision_token(a,('source',))==before
    payload['text']='changed'
    a.execute('UPDATE source_excerpts SET result_json=?',(json.dumps(payload),)); a.commit()
    assert cache.revision_token(a,('source',))!=before
    a.close(); b.close()


def test_new_tables_and_analysis_change(tmp_path):
    db=database(tmp_path/'a.sqlite'); first=cache.revision_token(db)
    db.execute('CREATE TABLE bulk_baseline_cache(id TEXT,result_json TEXT)'); db.commit()
    second=cache.revision_token(db)
    assert second!=first
    db.execute("INSERT INTO bulk_baseline_cache VALUES('1','{}')"); db.commit()
    assert cache.revision_token(db)!=second
    db.close()


def test_workflow_progress_does_not_invalidate_but_accepted_evidence_does(tmp_path):
    db=database(tmp_path/'workflow.sqlite')
    db.execute('CREATE TABLE strategic_workflow_runs(id TEXT PRIMARY KEY,status TEXT,error TEXT)')
    db.execute('CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT)')
    db.commit()
    before=cache.revision_token(db,('analysis',))
    db.execute("INSERT INTO strategic_workflow_runs VALUES('run','running','')")
    db.execute("INSERT INTO strategic_workflow_artifacts VALUES('run','keywords','{}')")
    db.execute("INSERT INTO strategic_workflow_artifacts VALUES('run','final','{}')")
    db.commit()
    assert cache.revision_token(db,('analysis',))==before
    db.execute("UPDATE strategic_workflow_runs SET status='complete'");db.commit()
    accepted=cache.revision_token(db,('analysis',))
    assert accepted!=before
    db.execute("UPDATE strategic_workflow_artifacts SET payload_json='changed' WHERE stage='final'");db.commit()
    changed=cache.revision_token(db,('analysis',))
    assert changed!=accepted
    db.execute("UPDATE strategic_workflow_runs SET error='withdrawn'");db.commit()
    assert cache.revision_token(db,('analysis',))!=changed
    db.close()


def test_isolation_singleflight_and_size_bound(tmp_path):
    path=tmp_path/'a.sqlite'; db=database(path)
    revision=cache.revision_token(db); db.close()
    entered=threading.Event(); release=threading.Event(); calls=[]
    def task():
        connection=sqlite3.connect(path)
        def build():
            calls.append(1); entered.set(); release.wait(2)
            return {'rows':[{'label':'original'}]}
        try: return cache.cached_read(connection,'parallel',revision,build)
        finally: connection.close()
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(task); assert entered.wait(2)
        b=pool.submit(task); release.set(); first=a.result(); second=b.result()
    assert len(calls)==1
    first['rows'][0]['label']='mutated'
    assert second['rows'][0]['label']=='original'
    db=sqlite3.connect(path)
    with patch.object(cache,'_MAX_ENTRIES',2):
        for n in range(3): cache.cached_read(db,'bounded',n,lambda:{'n':n})
        assert len(cache._CACHE)<=2
    db.close()


def test_memory_and_open_transaction_bypass(tmp_path):
    db=sqlite3.connect(':memory:'); assert cache.revision_token(db) is None
    db.close(); db=database(tmp_path/'a.sqlite')
    db.execute("INSERT INTO articles VALUES(1,'pending')")
    assert cache.revision_token(db) is None
    assert db.in_transaction
    db.rollback(); db.close()


def test_preview_caps_preserve_full_retrieval_and_invalidate(tmp_path):
    db=database(tmp_path/'a.sqlite')
    def sources(connection):
        return [{'kind':'graph','id':'one','row':{},'result':{
            'nodes':[{'id':str(i),'name':'node '+str(i),'type':'Concept','evidence_ids':['e'+str(i)]} for i in range(30)],
            'edges':[], 'evidence':[{'id':'e'+str(i),'text':'evidence '+str(i),'source_url':'https://example.org/'+str(i)} for i in range(30)]}}],{}
    with patch('graph_rag._analysis_sources',side_effect=sources) as build:
        graph=load_integrated_graph(db,{'max_nodes':['16'],'evidence_limit':['3']})
        assert len(graph['nodes'])==16 and len(graph['evidence'])==3
        assert len(graph.full_nodes)==30 and len(graph.full_evidence)==30
        graph['nodes'][0]['name']='mutated'
        other=load_integrated_graph(db,{'view':['preview'],'max_nodes':['24'],'evidence_limit':['12']})
        assert len(other['nodes'])==24 and len(other['evidence'])==12
        assert all(node['name']!='mutated' for node in other.full_nodes)
        assert build.call_count==1
        db.execute("INSERT INTO articles VALUES(1,'changed evidence')"); db.commit()
        load_integrated_graph(db,{})
        assert build.call_count==2
    db.close()


def test_baseline_current_source_gate_survives_cached_verification(tmp_path):
    from baseline_graph import baseline_sources
    from bulk_baseline import freeze_item,digest,BulkBaselineService
    db=database(tmp_path/'baseline.sqlite')
    db.execute('CREATE TABLE bulk_baseline_cache(input_hash TEXT,result_json TEXT,prepared_json TEXT)')
    item={'title':'소버린 AI 정부 투자','text':'정부가 소버린 AI 투자를 발표했다.','source_url':'https://example.org/news'}
    snapshot=freeze_item(item); refs=[e['id'] for e in snapshot['evidence']]
    report=dict(document_id=snapshot['document_id'],summary='소버린 AI 투자 발표',
                keywords=[dict(label='소버린 AI',source_quote='소버린 AI')],strategic_relevance='정책 관찰',
                risk_signal='위험 정보 미제시',limitations='발췌 분석',evidence_ids=refs)
    result=dict(report,verified=True,input_hash=snapshot['input_hash'],evidence=snapshot['evidence'],
                verification=dict(accepted=True,issues=[],checked_evidence_ids=refs,report_hash=digest(report),evidence_hash=digest(snapshot['evidence'])))
    db.execute('INSERT INTO bulk_baseline_cache VALUES(?,?,?)',(snapshot['input_hash'],json.dumps(result),json.dumps({'keywords':[{'label':'소버린 AI','surface':'소버린 AI'}]})))
    db.commit(); cache.revision_token(db)
    with patch.object(BulkBaselineService,'_valid_cached',wraps=BulkBaselineService._valid_cached) as validate:
        assert baseline_sources(db,[item])[1]['completed_verified_baselines']==1
        assert baseline_sources(db,[item])[1]['completed_verified_baselines']==1
        assert validate.call_count==1
        assert baseline_sources(db,[dict(item,text='투자 철회')])[0]==[]
    result['summary']='변조된 요약'
    db.execute('UPDATE bulk_baseline_cache SET result_json=?',(json.dumps(result),)); db.commit()
    assert baseline_sources(db,[item])[0]==[]
    db.close()


def test_graph_pressure_cannot_evict_dataset_bucket(tmp_path):
    db=database(tmp_path/'pressure.sqlite')
    token=cache.revision_token(db)
    with cache._LOCK:
        for key in list(cache._CACHE): cache._remove(key)
    calls=[]
    def dataset_builder():
        calls.append(1); return {'text':'d'*200}
    with patch.dict(cache._BUCKET_LIMITS,{'dataset':2048,'graph':2048}), patch.object(cache,'_MAX_BYTES',4096):
        cache.cached_read(db,'strategy-dataset-v1',token,dataset_builder)
        for n in range(10):
            cache.cached_read(db,'integrated_graph',n,lambda:{'text':'g'*200})
        cache.cached_read(db,'strategy-dataset-v1',token,dataset_builder)
    assert len(calls)==1
    assert cache.cache_info()['counters'].get('integrated_graph:eviction',0)>0
    db.close()
