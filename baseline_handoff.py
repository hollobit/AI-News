"""Move new inputs ahead of backlog without changing frozen runs or retry budgets."""
import json


def extraction_token(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='state'").fetchone():return None
    row=db.execute("SELECT value FROM state WHERE key='collector_corpus_snapshot'").fetchone()
    value=json.loads(row[0]) if row else {}
    return value.get('extracted_at') if value.get('status')=='complete' else None


def changed_source_documents(db, rows):
    """Check only the source text consumed by these frozen, unclaimed inputs."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_excerpts'").fetchone():return []
    from link_groups import canonical_url
    from source_enrichment import source_map
    from bulk_baseline import source_excerpt_text
    snapshots=[json.loads(row['snapshot_json']) for row in rows]
    targets=[]
    for snapshot in snapshots:
        urls={canonical_url(e.get('url') or '') for e in snapshot['evidence']}
        urls.discard('')
        # Automatic corpus entries have a single primary URL. Other selectors
        # keep their own frozen evidence contract.
        if len(urls)==1:targets.append((snapshot,urls.pop()))
    sources=source_map(db,[url for _,url in targets])
    changed=[]
    for snapshot,url in targets:
        source=sources.get(url,{})
        text=source_excerpt_text(source)
        frozen=[e['text'] for e in snapshot['evidence'] if e.get('origin')=='fetched_url_excerpt']
        if frozen!=([text] if text else []):changed.append(snapshot['document_id'])
    return changed


def can_carry(db, run):
    """Content rejections may wait separately; operator/engine pauses may not."""
    if run['status']=='requires_review':return True
    if run['status']!='paused':return False
    event=db.execute("SELECT seq FROM bulk_baseline_events WHERE run_id=? AND stage='input_handoff' ORDER BY seq DESC LIMIT 1",(run['id'],)).fetchone()
    if not event:return False
    return not db.execute("SELECT 1 FROM bulk_baseline_events WHERE run_id=? AND seq>? AND stage IN ('user_pause_requested','engine_paused')",(run['id'],event[0])).fetchone()


def can_resume_handoff(db, run, token):
    """Resume only unfinished work from the latest automatic input handoff."""
    from recursive_improvement import owner_alive
    latest=db.execute('SELECT id FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if (not run or not token or not latest or latest[0]!=run['id'] or
            run['status']!='paused' or run['error']!='new_input_handoff' or
            owner_alive(run['owner_pid']) or not can_carry(db,run) or extraction_token(db)!=token):
        return False
    return bool(db.execute('''SELECT 1 FROM bulk_baseline_documents d
        LEFT JOIN baseline_retry_budgets b ON b.run_id=d.run_id AND b.document_id=d.document_id
        WHERE d.run_id=? AND d.status IN ('pending','retry') AND d.attempts<COALESCE(b.attempt_limit,2)
        LIMIT 1''',(run['id'],)).fetchone())


def prior_rows(db, identity):
    from recursive_improvement import owner_alive
    run=db.execute('SELECT * FROM bulk_baseline_runs WHERE id=?',(identity,)).fetchone()
    latest=db.execute('SELECT id FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    if not run or not latest or latest[0]!=identity or owner_alive(run['owner_pid']) or not can_carry(db,run):
        raise ValueError('이전 실행의 중지·검토 상태를 보존해야 하므로 입력 인계를 시작할 수 없습니다.')
    return {r['document_id']:dict(r) for r in db.execute('''SELECT d.*,COALESCE(b.attempt_limit,2) AS attempt_limit
        FROM bulk_baseline_documents d LEFT JOIN baseline_retry_budgets b ON b.run_id=d.run_id AND b.document_id=d.document_id
        WHERE d.run_id=?''',(identity,))}


def request_handoff(service, run_id, token, *, changed_sources=None):
    """Only after already-running batches finish; pauses/errors take precedence."""
    from bulk_baseline import now,js
    with service.db() as db:
        row=db.execute("SELECT seq,stage FROM bulk_baseline_events WHERE run_id=? AND stage IN ('user_pause_requested','engine_paused','execution_resumed') ORDER BY seq DESC LIMIT 1",(run_id,)).fetchone()
        if row and row['stage']!='execution_resumed':return False
        detail={'extracted_at':token,'preserve_attempts':True}
        if changed_sources:detail.update(reason='source_changed',documents=changed_sources)
        db.execute('INSERT INTO bulk_baseline_events(run_id,stage,detail,created_at) VALUES(?,?,?,?)',
                   (run_id,'input_handoff',js(detail),now()))
    return True
