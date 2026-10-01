"""Coordinate source refreshes with corpus work without declaring them verified."""
import json
from link_groups import canonical_url


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS source_review_history(
        seq INTEGER PRIMARY KEY,url TEXT,hash TEXT,status TEXT,run_id TEXT,error TEXT,updated_at TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS source_review_pending ON source_reanalysis(status,updated_at)')


def archive(db, url):
    db.execute('''INSERT INTO source_review_history(url,hash,status,run_id,error,updated_at)
        SELECT url,hash,status,run_id,error,updated_at FROM source_reanalysis WHERE url=?''',(url,))


def corpus_owns(db, url):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='corpus_completion_documents'").fetchone():
        return False
    # Delegation is not publication or admission: corpus still checks its exact
    # Telegram context, current URL excerpt, independent audits and retry budget.
    return bool(db.execute('''SELECT 1 FROM corpus_completion_documents
        WHERE json_extract(snapshot_json,'$.source_url')=? LIMIT 1''',(canonical_url(url),)).fetchone())


def classify(db, row):
    if corpus_owns(db,row['url']):
        return 'delegated_corpus'
    versions=db.execute('SELECT COUNT(*) FROM source_versions WHERE url=?',(row['url'],)).fetchone()[0]
    if versions<=1 and not row['run_id']:
        return 'indexed'
    return 'pending'


def engine_blocked():
    from llm_runtime import LLMRuntime
    from llm_recovery import synchronize
    return bool(synchronize(LLMRuntime())['blocked'])


def recovered_after(updated_at):
    """An infrastructure failure needs a newer successful probe, not a timer retry."""
    from datetime import datetime
    from llm_runtime import LLMRuntime
    from llm_recovery import state
    try:
        failed_at=datetime.fromisoformat(updated_at).timestamp()
    except (TypeError,ValueError):
        return False
    return state(LLMRuntime())['success_at']>failed_at
