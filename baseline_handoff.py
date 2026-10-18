"""Move new inputs ahead of backlog without changing frozen runs or retry budgets."""
import json


def extraction_token(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='state'").fetchone():return None
    row=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()
    value=json.loads(row[0]) if row else {}
    return value.get('extracted_at') if value.get('status')=='complete' else None


def can_carry(db, run):
    """Content rejections may wait separately; operator/engine pauses may not."""
    if run['status']=='requires_review':return True
    if run['status']!='paused':return False
    event=db.execute("SELECT seq FROM bulk_baseline_events WHERE run_id=? AND stage='input_handoff' ORDER BY seq DESC LIMIT 1",(run['id'],)).fetchone()
    if not event:return False
    return not db.execute("SELECT 1 FROM bulk_baseline_events WHERE run_id=? AND seq>? AND stage IN ('user_pause_requested','engine_paused')",(run['id'],event[0])).fetchone()


def prior_rows(db, identity):
    from recursive_improvement import owner_alive
    run=db.execute('SELECT * FROM bulk_baseline_runs WHERE id=?',(identity,)).fetchone()
    latest=db.execute('SELECT id FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if not run or not latest or latest[0]!=identity or owner_alive(run['owner_pid']) or not can_carry(db,run):
        raise ValueError('이전 실행의 중지·검토 상태를 보존해야 하므로 입력 인계를 시작할 수 없습니다.')
    return {r['document_id']:dict(r) for r in db.execute('''SELECT d.*,COALESCE(b.attempt_limit,2) AS attempt_limit
        FROM bulk_baseline_documents d LEFT JOIN baseline_retry_budgets b ON b.run_id=d.run_id AND b.document_id=d.document_id
        WHERE d.run_id=?''',(identity,))}


def request_handoff(service, run_id, token):
    """Only after already-running batches finish; pauses/errors take precedence."""
    from bulk_baseline import now,js
    with service.db() as db:
        row=db.execute("SELECT seq,stage FROM bulk_baseline_events WHERE run_id=? AND stage IN ('user_pause_requested','engine_paused','execution_resumed') ORDER BY seq DESC LIMIT 1",(run_id,)).fetchone()
        if row and row['stage']!='execution_resumed':return False
        db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES(?,?,?,?)',
                   (run_id,'input_handoff',js({'extracted_at':token,'preserve_attempts':True}),now()))
    return True
