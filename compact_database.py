"""Offline SQLite compaction with integrity/row-identity verification and backup.

Stop every reader/writer first. The original database remains as a hard-linked
backup; the verified compact database replaces the main path atomically.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time


def identities(db):
    result={}
    tables=list(db.execute('PRAGMA table_list'))
    for schema,name,kind,_,without_rowid,*_ in tables:
        if schema!='main' or kind!='table' or name.startswith('sqlite_'):continue
        quoted='"'+name.replace('"','""')+'"'
        count=db.execute('SELECT count(*) FROM '+quoted).fetchone()[0]
        digest=hashlib.sha256()
        if not without_rowid:
            info=list(db.execute('PRAGMA table_info('+quoted+')'))
            pk=[r[1] for r in sorted(info,key=lambda r:r[5]) if r[5]]
            columns=','.join('"'+c.replace('"','""')+'"' for c in pk) if pk else '*'
            for row in db.execute('SELECT rowid,'+columns+' FROM '+quoted+' ORDER BY rowid'):
                digest.update(repr(tuple(row)).encode())
        result[name]=(count,digest.hexdigest())
    return result


def compact(path,backup):
    path=Path(path).resolve();backup=Path(backup).resolve()
    if not path.is_file() or backup.exists():raise ValueError('Source must exist and backup must be new')
    if path.parent.stat().st_dev!=backup.parent.stat().st_dev:raise ValueError('Backup must share the database filesystem')
    candidate=path.with_name(path.name+'.compact-'+str(os.getpid()))
    if candidate.exists():raise ValueError('Compaction candidate already exists')
    before=path.stat().st_size;started=time.monotonic()
    if shutil.disk_usage(path.parent).free<before*2:raise RuntimeError('Insufficient compaction workspace')
    try:
        with closing(sqlite3.connect(path,timeout=1)) as db:
            if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:raise RuntimeError('Database is still in use')
            db.execute('PRAGMA locking_mode=EXCLUSIVE')
            db.execute('BEGIN EXCLUSIVE');db.commit()
            original=identities(db)
            db.execute('VACUUM INTO ?',(str(candidate),))
            with closing(sqlite3.connect(candidate)) as check:
                if check.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RuntimeError('Compacted database integrity check failed')
                if identities(check)!=original:raise RuntimeError('Compaction changed row identities; original retained')
            # Keep the exact checkpointed original inode as the recovery copy.
            os.link(path,backup)
        os.replace(candidate,path)
        with closing(sqlite3.connect(path)) as db:db.execute('PRAGMA journal_mode=WAL')
        return dict(bytes_before=before,bytes_after=path.stat().st_size,backup=str(backup),tables_verified=len(original),seconds=round(time.monotonic()-started,3))
    finally:candidate.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/news.sqlite3');parser.add_argument('--backup',required=True)
    args=parser.parse_args();print(json.dumps(compact(args.db,args.backup)),flush=True)
