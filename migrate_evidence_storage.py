"""Bounded, resumable lossless compaction. Run after all readers use blob-aware code."""
import argparse
import json
import sqlite3
from evidence_blobs import migrate_batch

TARGETS=(('improvement_catalog','payload_json'),('improvement_catalog_history','payload_json'),
         ('improvement_ingestions','result_json'),('bulk_baseline_documents','snapshot_json'),
         ('bulk_baseline_documents','prepared_json'),('bulk_baseline_documents','result_json'))


def run(path, batches=1, size=100):
    if batches<1 or not 1<=size<=1000:raise ValueError('Invalid bounded migration size')
    db=sqlite3.connect(path,timeout=30)
    try:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        stats=[]
        for table,column in TARGETS:
            if table not in tables:continue
            total={'table':table,'column':column,'scanned':0,'converted':0,'column_bytes_before':0,'column_bytes_after':0}
            for _ in range(batches):
                with db:result=migrate_batch(db,table,column,size)
                for key,value in result.items():total[key]+=value
                if result['scanned']<size:break
            stats.append(total)
        return stats
    finally:db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/news.sqlite3')
    parser.add_argument('--batches',type=int,default=1)
    parser.add_argument('--batch-size',type=int,default=100)
    args=parser.parse_args()
    print(json.dumps(run(args.db,args.batches,args.batch_size),ensure_ascii=False))
