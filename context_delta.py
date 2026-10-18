"""Exact analysis publication deltas; source or policy edits require full admission."""
import json
import uuid

LIMIT=10000


def install(db):
    from context_paper_delta import install as install_papers
    install_papers(db)
    from context_workflow_delta import install as install_workflows
    install_workflows(db)
    columns={r[1] for r in db.execute('PRAGMA table_info(bulk_baseline_cache)')}
    if 'input_hash' not in columns:return
    db.execute('CREATE TABLE IF NOT EXISTS baseline_search_state(id INTEGER PRIMARY KEY,epoch TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO baseline_search_state VALUES(1,?)',(uuid.uuid4().hex,))
    db.execute('CREATE TABLE IF NOT EXISTS baseline_search_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,old_hash TEXT,new_hash TEXT)')
    for event in ('INSERT','UPDATE','DELETE'):
        old='NULL' if event=='INSERT' else 'OLD.input_hash'
        new='NULL' if event=='DELETE' else 'NEW.input_hash'
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS baseline_search_v1_{event.lower()} AFTER {event} ON bulk_baseline_cache BEGIN
            INSERT INTO baseline_search_changes(old_hash,new_hash) VALUES({old},{new});
            DELETE FROM baseline_search_changes WHERE seq<=(SELECT MAX(seq)-{LIMIT} FROM baseline_search_changes);
            END''')


def position(db):
    from projection_cache import revision_token
    from context_index import wiki_revision
    from evidence_corrections import correction_token
    from verified_cache import policy
    token=revision_token(db) if not db.in_transaction else None
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='baseline_search_changes'").fetchone():return None
    if not token and not db.in_transaction:return None
    values={r[0]:(r[1],r[2]) for r in db.execute('SELECT kind,value,identity FROM projection_revisions')}
    from context_paper_delta import position as paper_position
    from context_workflow_delta import position as workflow_position
    return dict(epoch=db.execute('SELECT epoch FROM baseline_search_state WHERE id=1').fetchone()[0],
                seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM baseline_search_changes').fetchone()[0],
                source=values['source'],analysis=values['analysis'],schema=db.execute('PRAGMA schema_version').fetchone()[0],
                policy=policy(),corrections=correction_token(db),wiki=wiki_revision(db),paper=paper_position(db),workflow=workflow_position(db))


def changed_keys(db,previous,current):
    changes=publication_changes(db,previous,current)
    return changes['baseline'] if changes is not None and not changes['paper'] and not changes['workflow'] else None


def publication_changes(db,previous,current):
    if not previous or not current:return None
    # JSON normalization handles tuple/list round-trips without changing meaning.
    def stable(value):return json.dumps({k:v for k,v in value.items() if k not in ('seq','analysis','paper','workflow')},sort_keys=True)
    if stable(previous)!=stable(current) or previous['analysis'][1]!=current['analysis'][1]:return None
    from context_paper_delta import changes as paper_changes
    papers=paper_changes(db,previous.get('paper'),current.get('paper'))
    if papers is None:return None
    from context_workflow_delta import changes as workflow_changes
    workflows=workflow_changes(db,previous.get('workflow'),current.get('workflow'))
    if workflows is None:return None
    delta=current['seq']-previous['seq']
    if not 0<=delta<=256 or current['analysis'][0]-previous['analysis'][0]!=delta+papers[0]+workflows[0]:return None
    rows=db.execute('SELECT seq,old_hash,new_hash FROM baseline_search_changes WHERE seq>? AND seq<=? ORDER BY seq',
                    (previous['seq'],current['seq'])).fetchall()
    if len(rows)!=delta:return None
    return dict(baseline={key for row in rows for key in row[1:] if key},paper=papers[1],workflow=workflows[1])


def inputs(db,previous,current):
    if not previous:return None
    import sqlite3
    from baseline_graph import baseline_sources
    from graph_disk_index import encode
    try:
        with sqlite3.connect(previous.resolve().as_uri()+'?mode=ro',uri=True) as store:
            meta=dict(store.execute("SELECT key,body FROM meta WHERE key IN ('baseline_delta','coverage','workspace_format')"))
            from context_index import VERSION
            if meta.get('workspace_format')!=VERSION:return None
            changes=publication_changes(db,json.loads(meta.get('baseline_delta','null')),current)
            if changes is None:return None
            keys=changes['baseline']
            coverage=json.loads(meta['coverage'])
            # A historical partial workflow needs a full recount of excluded
            # claims. Never approximate coverage by counting visible records.
            if changes['workflow'] and coverage.get('stale_workflow_claims_excluded'):return None
            source_keys={encode(['baseline',key,None]) for key in keys}
            old_count=sum(1 for row in store.execute("SELECT source_key FROM sources WHERE kind='baseline'") if row[0] in source_keys)
            workflow_keys={encode(['workflow',key,None]) for key in changes['workflow']}
            old_workflows=sum(1 for row in store.execute("SELECT source_key FROM sources WHERE kind='workflow'") if row[0] in workflow_keys)
        sources,_=baseline_sources(db,input_hashes=keys) if keys else ([],{})
        coverage['baseline_cached_analyses']=db.execute('SELECT count(*) FROM bulk_baseline_cache').fetchone()[0]
        coverage['completed_verified_baselines']=coverage.get('completed_verified_baselines',0)-old_count+len(sources)
        coverage['baseline_stale_or_invalid']=coverage['baseline_cached_analyses']-coverage['completed_verified_baselines']
        if changes['paper']:
            from paper_graph import paper_sources
            papers,counts=paper_sources(db)
            # Coverage uses the exact existing validation gate, including failed,
            # stale and unpublished papers; only changed bodies enter the index.
            sources.extend(p for p in papers if p['id'] in changes['paper'])
            source_keys.update(encode(['paper',key,None]) for key in changes['paper'])
            coverage.update(paper_analyses=sum(counts[k] for k in ('verified','needs_review','stale','pending','failed')),
                            completed_verified_papers=counts['verified'],papers_needing_review=counts['needs_review'],
                            stale_paper_analyses=counts['stale'])
        if changes['workflow']:
            from graph_rag import _analysis_sources
            workflows,counts=_analysis_sources(db,selected={'workflow':changes['workflow']})
            sources.extend(workflows);source_keys.update(workflow_keys)
            coverage.update(strategic_workflow_runs=counts['strategic_workflow_runs'],
                            completed_verified_workflows=coverage.get('completed_verified_workflows',0)-old_workflows+len(workflows),
                            stale_workflow_claims_excluded=counts['stale_workflow_claims_excluded'])
        return sources,coverage,source_keys
    except (sqlite3.Error,ValueError,KeyError,TypeError):return None
