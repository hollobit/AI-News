"""Incremental admission into a live deep-analysis cycle, preserving its history."""
import json


def reconcile(db,cycle_id,corpus):
    from corpus_completion import encoded,now
    from recursive_improvement import news_identity,news_fingerprint
    cycle=db.execute('SELECT state_json,seen_json,pause_requested FROM rsi_cycles WHERE id=?',(cycle_id,)).fetchone()
    if not cycle or cycle['pause_requested']:return {'added':0,'changed':0,'deferred':0,'paused':True}
    state,seen=json.loads(cycle['state_json']),json.loads(cycle['seen_json'])
    current={news_identity(item):item for item in corpus}
    old={r['document_id']:dict(r) for r in db.execute('SELECT * FROM corpus_completion_documents WHERE cycle_id=?',(cycle_id,))}
    active=set()
    for r in db.execute("SELECT snapshot_json FROM rsi_rounds WHERE cycle_id=? AND status IN ('planned','running')",(cycle_id,)):
        active.update(json.loads(r[0]).get('identities',[]))
    db.execute('''CREATE TABLE IF NOT EXISTS completion_input_history (
        seq INTEGER PRIMARY KEY,cycle_id TEXT,document_id TEXT,prior_json TEXT,changed_at TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS completion_input_events (
        seq INTEGER PRIMARY KEY,cycle_id TEXT,added INTEGER,changed INTEGER,deferred INTEGER,created_at TEXT)''')
    position=max([r['position'] for r in old.values()]+[-1])+1
    added=changed=deferred=0
    for identity,item in current.items():
        previous=old.get(identity)
        if previous and news_fingerprint(item)==news_fingerprint(json.loads(previous['snapshot_json'])):continue
        if identity in active:
            deferred+=1;continue
        if previous:
            db.execute('INSERT INTO completion_input_history(cycle_id,document_id,prior_json,changed_at) VALUES(?,?,?,?)',
                       (cycle_id,identity,encoded(previous),now()))
            db.execute("UPDATE corpus_completion_documents SET snapshot_json=?,status='pending',attempts=0,workflow_run_id=NULL,admission_json=?,updated_at=? WHERE cycle_id=? AND document_id=?",
                       (encoded(item),encoded({'reason':'changed_input','prior_attempts':previous['attempts']}),now(),cycle_id,identity))
            state.pop(identity,None);seen.pop(identity,None);changed+=1
        else:
            db.execute('INSERT INTO corpus_completion_documents VALUES (?,?,?,?,?,0,NULL,?,?)',
                       (cycle_id,identity,position,encoded(item),'pending',encoded({'reason':'new_corpus_document'}),now()))
            position+=1;added+=1
    coverage=dict(getattr(corpus,'coverage',{}),corpus_ids=list(current))
    db.execute('UPDATE rsi_cycles SET coverage_json=?,state_json=?,seen_json=? WHERE id=?',
               (encoded(coverage),encoded(state),encoded(seen),cycle_id))
    if added or changed or deferred:
        db.execute('INSERT INTO completion_input_events(cycle_id,added,changed,deferred,created_at) VALUES(?,?,?,?,?)',
                   (cycle_id,added,changed,deferred,now()))
    return dict(added=added,changed=changed,deferred=deferred)


def refresh(runner,stamp):
    """Cheap token check, full admission only on extraction changes or deferred edits."""
    if stamp<getattr(runner,'_inputs_next',0):return
    from corpus_completion import connect
    from baseline_handoff import extraction_token
    from improvement_selection import all_corpus_items
    runner._inputs_next=stamp+2
    with connect(runner.path) as db:
        token=extraction_token(db)
        if not token or (token==getattr(runner,'_inputs_token',None) and not getattr(runner,'_inputs_deferred',False)):return
        corpus=all_corpus_items(db)
        db.execute('BEGIN IMMEDIATE')
        result=reconcile(db,runner.cycle_id,corpus)
    runner._inputs_token=token;runner._inputs_deferred=bool(result['deferred'])
    # Edits to a running article wait for that article's checkpoint, not the
    # 30-minute cycle boundary. Bound repeated scans while a model is busy.
    if runner._inputs_deferred:runner._inputs_next=stamp+10


def has_new_inputs(db,cycle_id):
    from improvement_selection import all_corpus_items
    from recursive_improvement import news_identity,news_fingerprint
    old={r['document_id']:news_fingerprint(json.loads(r['snapshot_json'])) for r in
         db.execute('SELECT document_id,snapshot_json FROM corpus_completion_documents WHERE cycle_id=?',(cycle_id,))}
    return any(old.get(news_identity(i))!=news_fingerprint(i) for i in all_corpus_items(db))
