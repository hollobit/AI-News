"""Bounded baseline scheduling and unpublished generation checkpoints."""
import json
import os

VERSION = 'baseline-execution-v1'
EFFICIENT = {'workers': 4, 'batch_size': 4, 'micro_batches': True, 'realtime_inputs': True}


def scheduled_settings():
    return dict(EFFICIENT) if os.getenv('NEWS_BASELINE_PROFILE', 'efficient') == 'efficient' else {'workers': 2, 'batch_size': 1}


def select_rows(rows, settings):
    """Never mix retries with new work; bound each batch's complete source text."""
    if not settings.get('micro_batches'):
        from bulk_baseline import choose_batch_size
        return rows[:choose_batch_size(rows, settings)]
    chosen = []
    characters = 0
    for row in rows:
        snapshot = json.loads(row['snapshot_json'])
        size = sum(len(e.get('text', '')) for e in snapshot['evidence'])
        # Failed items retain a small failure domain and their own feedback.
        if row['attempts'] or size > 2200:
            return chosen or [row]
        if len(chosen) >= min(4, settings['batch_size']) or characters + size > 7200:
            break
        chosen.append(row)
        characters += size
    return chosen


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS baseline_generation_checkpoints(
        key TEXT PRIMARY KEY, document_id TEXT NOT NULL, input_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS baseline_active_batches(
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner_pid INTEGER NOT NULL,
        documents INTEGER NOT NULL, started_at TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS baseline_retry_budgets(
        run_id TEXT NOT NULL,document_id TEXT NOT NULL,attempt_limit INTEGER NOT NULL,
        PRIMARY KEY(run_id,document_id))''')


def update_profile(current, changes):
    """Performance controls never change the frozen scope or retry budget."""
    if not isinstance(changes,dict) or set(changes)-{'workers','batch_size','micro_batches','realtime_inputs'}:
        raise ValueError('재개 시 작업자·배치·실시간 입력 설정만 변경할 수 있습니다.')
    result=dict(current)
    for name,maximum in [('workers',6),('batch_size',32)]:
        if name in changes:
            if type(changes[name]) is not int or not 1<=changes[name]<=maximum:
                raise ValueError(name+' 범위를 확인해 주세요.')
            result[name]=changes[name]
    for name in ('micro_batches','realtime_inputs'):
        if name in changes:
            if type(changes[name]) is not bool:raise ValueError(name+'는 boolean이어야 합니다.')
            result[name]=changes[name]
    return result


def key_for(service, snapshot, prepared, feedback):
    from bulk_baseline import digest, ANALYSIS_SCHEMA
    from model_policy import policy
    return digest([VERSION, snapshot, prepared, feedback, ANALYSIS_SCHEMA,
                   service._prompt('analysis', []), policy('baseline_analysis')])


def read_draft(service, key, snapshot, prepared):
    from bulk_baseline import validate_record
    with service.db() as db:
        row = db.execute('SELECT payload_json FROM baseline_generation_checkpoints WHERE key=?', (key,)).fetchone()
    if not row:
        return None
    try:
        saved = json.loads(row[0])
        validate_record(saved['record'], snapshot, prepared)
        if not all(k in saved['candidates'].values() for k in saved['record']['keywords']):
            return None
        return saved
    except (ValueError, TypeError, KeyError):
        return None


def save_draft(service, key, snapshot, record, candidates, provenance):
    from bulk_baseline import js, now
    payload = dict(record=record, candidates=candidates, provenance=provenance)
    with service.db() as db:
        db.execute('INSERT OR REPLACE INTO baseline_generation_checkpoints VALUES(?,?,?,?,?)',
                   (key, snapshot['document_id'], snapshot['input_hash'], js(payload), now()))
        db.execute('DELETE FROM baseline_generation_checkpoints WHERE key NOT IN '
                   '(SELECT key FROM baseline_generation_checkpoints ORDER BY created_at DESC LIMIT 512)')


def discard(db, key):
    db.execute('DELETE FROM baseline_generation_checkpoints WHERE key=?', (key,))
