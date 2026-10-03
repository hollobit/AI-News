"""Explicit one-document repair, retaining attempts, rejected output and normal review gates."""
import argparse
import fcntl
import json
import os
from pathlib import Path
from bulk_baseline import BulkBaselineService, hydrate_rows, freeze_item, now, js
from recursive_improvement import owner_alive
from improvement_selection import all_corpus_items


def repair(path, run_id, document_id, reason):
    if not reason.strip(): raise ValueError('보완 사유가 필요합니다.')
    with Path(str(Path(path).resolve())+'.baseline-worker.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        service=BulkBaselineService(path,lambda: [])
        try:
            with service.db() as db:
                run=db.execute('SELECT * FROM bulk_baseline_runs WHERE id=?',(run_id,)).fetchone()
                if not run or owner_alive(run['owner_pid']): raise RuntimeError('기존 기본 분석 소유자를 확인해 주세요.')
                row=db.execute('SELECT * FROM bulk_baseline_documents WHERE run_id=? AND document_id=?',(run_id,document_id)).fetchone()
                if not row or (row['status'] not in ('needs_review','failed') and not (row['status']=='pending' and row['attempts']>=2 and run['status']=='paused')) or row['attempts']>=4:
                    raise ValueError('보완 대상이 없거나 추가 보완 상한에 도달했습니다.')
                current=next((freeze_item(i) for i in all_corpus_items(db) if freeze_item(i)['document_id']==document_id),None)
                if not current or current['input_hash']!=row['input_hash']:
                    raise ValueError('현재 입력이 달라 기존 스냅샷 보완을 중단합니다.')
                rows=hydrate_rows(db,[row])
                db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES (?,?,?,?)',
                           (run_id,'explicit_document_repair',js({'reason':reason,'prior':dict(row)}),now()))
                db.execute("UPDATE bulk_baseline_documents SET status='running',attempts=attempts+1 WHERE run_id=? AND document_id=?",(run_id,document_id))
                db.execute("UPDATE bulk_baseline_runs SET status='running',owner_pid=?,updated_at=? WHERE id=?",(os.getpid(),now(),run_id))
            service._batch(run_id,rows)
            with service.db() as db:
                counts=dict(db.execute('SELECT status,count(*) FROM bulk_baseline_documents WHERE run_id=? GROUP BY status',(run_id,)))
            if service.stop.is_set():
                with service.db() as db:
                    error=db.execute('SELECT error FROM bulk_baseline_documents WHERE run_id=? AND document_id=?',(run_id,document_id)).fetchone()[0]
                service._status(run_id,'paused',error)
            else:
                service._status(run_id,'complete' if set(counts)=={'verified'} else 'requires_review')
            return {'run':run_id,'counts':counts}
        finally:
            service.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default='data/news.sqlite3');p.add_argument('--run',required=True)
    p.add_argument('--document',required=True);p.add_argument('--reason',required=True)
    args=p.parse_args()
    from app import load_local_env
    load_local_env()
    print(json.dumps(repair(args.db,args.run,args.document,args.reason),ensure_ascii=False),flush=True)
