"""Live collection coverage distinct from a frozen baseline run."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import sqlite3
import threading
from projection_cache import revision_token


class CorpusStatus:
    def __init__(self,path):
        self.path=str(path);self.lock=threading.Lock();self.pool=ThreadPoolExecutor(max_workers=1)
        self.key=None;self.pending=False;self.value={'status':'preparing'};self.closed=False

    def get(self):
        with sqlite3.connect(self.path,timeout=.5) as db:
            token=revision_token(db,('source',))
            run=db.execute('SELECT id,status FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        key=(token,tuple(run) if run else None)
        with self.lock:
            if not self.closed and not self.pending and key!=self.key:
                self.pending=True;self.pool.submit(self._build,key,run)
            return dict(self.value,refreshing=self.pending)

    def _build(self,key,run):
        try:
            from improvement_selection import all_corpus_items
            from database import open_db as connect
            db=connect(self.path)
            try:
                db.execute('BEGIN')
                items=all_corpus_items(db)
                ids={i['corpus_identity'] for i in items}
                frozen={r[0] for r in db.execute('SELECT document_id FROM bulk_baseline_documents WHERE run_id=?',(run[0],))} if run else set()
            finally:db.close()
            value={'status':'ready','total_unique':len(ids),'baseline_total':len(frozen),
                   'new_since_baseline':len(ids-frozen),'removed_since_baseline':len(frozen-ids),
                   'baseline_run_id':run[0] if run else None,'baseline_status':run[1] if run else None,
                   'computed_at':datetime.now(timezone.utc).isoformat(),
                   'scope':'수집된 Telegram 뉴스의 정규 URL 및 URL 없는 본문 중복 제거. 기본 분석은 실행 당시 고정한 별도 대상입니다.'}
            with self.lock:self.value=value;self.key=key
        except Exception:
            with self.lock:self.value=dict(self.value,status='failed',error='현재 수집량을 확인하지 못했습니다.')
        finally:
            with self.lock:self.pending=False

    def close(self):self.closed=True;self.pool.shutdown(wait=False,cancel_futures=True)
