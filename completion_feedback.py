"""Dry-run by default. Back up and update only never-started pending feedback."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sqlite3
from corpus_completion import historical_feedback


def backfill(database,cycle_id,*,apply=False,backup_path=None):
    database=Path(database).resolve()
    db=sqlite3.connect(str(database) if apply else f'file:{database}?mode=ro',uri=not apply,timeout=60)
    db.row_factory=sqlite3.Row
    try:
        if apply:db.execute('BEGIN IMMEDIATE')
        rows=db.execute("SELECT * FROM corpus_completion_documents WHERE cycle_id=? AND status='pending' AND attempts=0 ORDER BY position",(cycle_id,)).fetchall()
        items=[json.loads(row['snapshot_json']) for row in rows]
        feedback=historical_feedback(db,items)
        changes=[]
        for row in rows:
            old=json.loads(row['admission_json']);new=feedback.get(row['document_id'])
            if not new or old.get('issues'):continue
            merged=dict(old,**new)
            changes.append((dict(row),merged))
        backup=None
        if apply and changes:
            backup=Path(backup_path) if backup_path else Path('.runtime/verification')/('completion-feedback-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.jsonl')
            backup.parent.mkdir(parents=True,exist_ok=True)
            with backup.open('x',encoding='utf-8') as output:
                for row,new in changes:
                    output.write(json.dumps({'cycle_id':row['cycle_id'],'document_id':row['document_id'],
                        'previous_admission_json':row['admission_json'],'previous_updated_at':row['updated_at'],
                        'new_admission':new},ensure_ascii=False)+'\n')
                output.flush();os.fsync(output.fileno())
            for row,new in changes:
                changed=db.execute("UPDATE corpus_completion_documents SET admission_json=? WHERE cycle_id=? AND document_id=? AND status='pending' AND attempts=0 AND admission_json=?",
                    (json.dumps(new,ensure_ascii=False),cycle_id,row['document_id'],row['admission_json'])).rowcount
                if changed!=1:raise RuntimeError('Pending row changed; feedback transaction aborted')
            db.commit()
        elif apply:db.commit()
        return {'mode':'apply' if apply else 'dry_run','eligible_pending_rows':len(rows),'feedback_rows':len(changes),
                'backup':str(backup) if backup else None,'preserved':'running/completed rows, attempts, snapshots, workflow results, statuses'}
    except BaseException:
        db.rollback();raise
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/news.sqlite3');parser.add_argument('--cycle',required=True)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--backup')
    args=parser.parse_args()
    print(json.dumps(backfill(args.db,args.cycle,apply=args.apply,backup_path=args.backup),ensure_ascii=False))
