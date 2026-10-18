"""Coalesce a fresh headline batch with source reads already owned by deep work.

No fetching, model calls, snapshot mutation or retry-budget changes happen here.
The deadline belongs to the extraction event, so restarts cannot extend it.
"""
from datetime import datetime, timezone
import json
import math
import re
import time

WINDOW_SECONDS = 45
MAX_NEW_DOCUMENTS = 64
TERMINAL_SOURCE_STATES = {'fetched','failed','blocked','unsupported','unavailable'}


def headline_only(snapshot):
    if snapshot.get('source_scope') != 'telegram_excerpt_only':return False
    text=' '.join(e.get('text','') for e in snapshot['evidence'])
    text=text.replace(snapshot.get('title',''), '') if snapshot.get('title') else text
    text=re.sub(r'https?://\S+', '', text)
    # A scheduling hint, not an assertion about semantic evidence sufficiency.
    return len(re.sub(r'\W', '', text)) < 40


def deep_owner(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'risk_schedule','rsi_cycles'} <= tables:return None
    schedule=db.execute('SELECT enabled,launched_pid,cycle_id FROM risk_schedule WHERE id=1').fetchone()
    if not schedule or not schedule['enabled']:return None
    cycle=db.execute('SELECT id,owner_pid,pause_requested,status,error FROM rsi_cycles ORDER BY created_at DESC LIMIT 1').fetchone()
    if (not cycle or cycle['pause_requested'] or schedule['cycle_id'] != cycle['id']
            or cycle['status'] in ('paused','error','failed') or cycle['error']):return None
    from scheduled_risks import alive
    return next((pid for pid in (cycle['owner_pid'],schedule['launched_pid']) if alive(pid)),None)


def wait_for_sources(db, snapshots, pending, prior, extraction, now, *, progress=None,
                     clock=time.monotonic, sleep=time.sleep, owner=deep_owner):
    try:age=now.timestamp()-datetime.fromisoformat(extraction['extracted_at']).timestamp()
    except (KeyError,TypeError,ValueError):return None
    if not 0 <= age < WINDOW_SECONDS:return None
    fresh=[s for s in snapshots.values() if s['document_id'] in pending
           and prior.get(s['document_id']) != s['input_hash']]
    if not 1 <= len(fresh) <= MAX_NEW_DOCUMENTS:return None
    from source_enrichment import source_map
    from link_groups import canonical_url
    urls={canonical_url(e.get('url','')) for s in fresh if headline_only(s) for e in s['evidence']}
    urls.discard('')
    cached=source_map(db,urls)
    targets={url for url in urls if cached.get(url,{}).get('status') not in TERMINAL_SOURCE_STATES}
    # Do not hold a predominantly substantive batch behind a few missing URLs.
    if len(targets) < math.ceil(len(fresh)*.8) or not owner(db):return None
    started=clock();duration=WINDOW_SECONDS-age;remaining=set(targets)
    initial=db.execute('SELECT id,status FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    initial=tuple(initial) if initial else None
    reason='deadline'
    while clock()-started < duration:
        states=dict(db.execute("SELECT key,value FROM state WHERE key IN ('collector_corpus_snapshot','collector_last_success')"))
        current=json.loads(states.get('collector_corpus_snapshot','{}'))
        latest=db.execute('SELECT id,status FROM bulk_baseline_runs ORDER BY created_at DESC LIMIT 1').fetchone()
        if current.get('extracted_at') != extraction['extracted_at'] or current.get('status') != 'complete':
            reason='new_extraction';break
        if (tuple(latest) if latest else None) != initial:
            reason='baseline_state_changed';break
        try:heartbeat=datetime.fromisoformat(states['collector_last_success']).timestamp()
        except (KeyError,TypeError,ValueError):heartbeat=0
        if now.timestamp()+clock()-started-heartbeat > 120:
            reason='collector_stale';break
        live=owner(db)
        if not live:reason='source_owner_stopped';break
        cached=source_map(db,remaining)
        remaining={url for url in remaining if cached.get(url,{}).get('status') not in TERMINAL_SOURCE_STATES}
        if not remaining:reason='sources_ready';break
        elapsed=clock()-started
        if progress:
            progress(dict(policy='headline-source-window-v1',documents=len(targets),
                          ready_documents=len(targets)-len(remaining),pending_documents=len(remaining),
                          remaining_seconds=max(0,math.ceil(duration-elapsed)),owner_pid=live,
                          deadline=datetime.fromtimestamp(now.timestamp()+duration,timezone.utc).isoformat()))
        sleep(max(0,min(1,duration-elapsed)))
    return dict(policy='headline-source-window-v1',documents=len(targets),
                ready_documents=len(targets)-len(remaining),pending_documents=len(remaining),
                elapsed_seconds=round(clock()-started,3),reason=reason,maximum_seconds=WINDOW_SECONDS)
