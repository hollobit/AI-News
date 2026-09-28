import time
from app import connect,process_updates
from corpus_status import CorpusStatus


def test_current_collection_count_is_separate_from_frozen_run(tmp_path):
    path=tmp_path/'news.sqlite3'
    with connect(path) as db:
        db.execute('CREATE TABLE bulk_baseline_runs(id TEXT,status TEXT,created_at TEXT)')
        db.execute('CREATE TABLE bulk_baseline_documents(run_id TEXT,document_id TEXT)')
        db.execute("INSERT INTO bulk_baseline_runs VALUES ('old','complete','2026-09-15')")
        db.execute("INSERT INTO bulk_baseline_documents VALUES ('old','https://example.org/old')")
        for i,url in enumerate(['old','new'],1):
            process_updates(db,[{'update_id':i,'channel_post':{'chat':{'id':-100123,'type':'channel'},
                'message_id':i,'date':1789531200,'text':'AI 연구\nhttps://example.org/'+url}}],{'-100123'})
    service=CorpusStatus(path)
    try:
        deadline=time.monotonic()+4
        while True:
            value=service.get()
            if value['status']=='ready':break
            assert time.monotonic()<deadline,value
            time.sleep(.01)
        assert value['total_unique']==2
        assert value['baseline_total']==1
        assert value['new_since_baseline']==1
    finally:service.close()
