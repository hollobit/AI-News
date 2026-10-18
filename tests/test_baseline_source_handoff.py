"""Source arrivals cannot start another model batch on superseded evidence."""
import json
import threading
from copy import deepcopy
from unittest.mock import patch

import pytest

from baseline_handoff import changed_source_documents, can_carry
from bulk_baseline import freeze_item, unpack_json
from source_enrichment import init_sources, attach_sources
from tests import test_bulk_baseline as fixtures


def put_source(db, url, value):
    db.execute('INSERT OR REPLACE INTO source_excerpts VALUES (?,?,?)',
               (url,json.dumps(value),'2026-10-19T00:00:00+00:00'))


def source(text='새 원문 소버린 AI', **extra):
    return dict(status='fetched',text=text,**extra)


@pytest.mark.parametrize('before,after,changed',[
    ({},source(),True),
    (source(),source('수정 원문 소버린 AI'),True),
    (source(),dict(status='failed',text=''),True),
    (source(),None,True),
    (source(),source(title='새 제목',fetched_at='later'),False),
    (source('a'*600+'old'),source('a'*600+'new'),False),
    ({},dict(status='failed',text='오류'),False),
])
def test_only_consumed_source_text_changes_the_frozen_input(tmp_path,before,after,changed):
    import sqlite3
    with sqlite3.connect(tmp_path/'news.db') as db:
        init_sources(db)
        item=dict(title='기사',text='메시지',source_url='https://example.com/story',source_context=before)
        snapshot=freeze_item(item);original=deepcopy(snapshot)
        if after is not None:put_source(db,item['source_url'],after)
        rows=[dict(snapshot_json=json.dumps(snapshot))]
        assert changed_source_documents(db,rows)==([snapshot['document_id']] if changed else [])
        assert snapshot==original
        current=freeze_item(dict(item,source_context=after or {}))
        assert (current['input_hash']!=snapshot['input_hash'])==changed


def test_missing_source_table_url_free_and_multiple_url_snapshots_decline(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path/'news.db') as db:
        snapshot=freeze_item(dict(title='뉴스',text='내용'))
        rows=[dict(snapshot_json=json.dumps(snapshot))]
        assert changed_source_documents(db,rows)==[]
        init_sources(db)
        assert changed_source_documents(db,rows)==[]
        snapshot['evidence'].extend([dict(url='https://example.com/a'),dict(url='https://example.com/b')])
        assert changed_source_documents(db,[dict(snapshot_json=json.dumps(snapshot))])==[]


@pytest.fixture
def case():
    case=fixtures.BaselineTests();case.setUp();case.items=case.items[:2]
    service=case.service()
    with service.db() as db:
        init_sources(db)
        db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO state VALUES (?,?)',('collector_corpus_snapshot',
            json.dumps(dict(status='complete',extracted_at='2026-10-19T00:00:00+00:00'))))
    def select():
        with service.db() as db:return attach_sources(db,case.items)
    service.selector=select
    try:yield case,service
    finally:case.tearDown()


def test_new_source_before_claim_skips_models_and_carries_original_scope(case):
    case,service=case
    launch=service._launch
    def arrived(run):
        with service.db() as db:put_source(db,case.items[0]['source_url'],source())
        launch(run)
    with patch.object(service,'_launch',side_effect=arrived):
        old=case.done(service,service.start(dict(workers=1,batch_size=2,realtime_inputs=True))['id'])
    assert old['status']=='paused' and old['error']=='new_input_handoff'
    assert not case.calls and old['metrics']['total']==2
    with service.db() as db:
        before=[tuple(r) for r in db.execute('SELECT * FROM bulk_baseline_documents WHERE run_id=?',(old['id'],))]
        assert all(r[6]=='pending' and r[7]==0 for r in before)
        detail=json.loads(db.execute("SELECT detail FROM bulk_baseline_events WHERE stage='input_handoff'").fetchone()[0])
        assert detail['reason']=='source_changed' and detail['documents']==[case.items[0]['source_url']]
        assert can_carry(db,old)
    latest=case.done(service,service.start(dict(workers=1,batch_size=2,realtime_inputs=True,carry_forward_run=old['id']))['id'])
    assert latest['status']=='complete' and latest['metrics']['verified']==2
    assert len(case.calls)==2
    with service.db() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM bulk_baseline_documents WHERE run_id=?',(old['id'],))]==before
        frozen=unpack_json(db,db.execute('SELECT snapshot_json FROM bulk_baseline_documents WHERE run_id=? AND document_id=?',
            (latest['id'],case.items[0]['source_url'])).fetchone()[0])
        assert frozen['source_scope']=='telegram_and_cached_url_excerpt'
    for prompt in case.calls:
        documents=json.loads(prompt.split('DATA:\n')[1])
        assert next(d for d in documents if d['document_id']==case.items[0]['source_url'])['evidence']==frozen['evidence']


@pytest.mark.parametrize('interruption',['none','user_pause','engine_error'])
def test_inflight_review_finishes_and_explicit_pause_or_failure_wins(case,interruption):
    case,service=case
    entered=threading.Event();release=threading.Event()
    def model(prompt,schema):
        if prompt.startswith('ROLE: baseline_analysis'):
            entered.set();assert release.wait(4)
            if interruption=='engine_error':raise RuntimeError('[engine:network]')
        return case.model(prompt,schema)
    service.analyzer=model
    try:
        run=service.start(dict(workers=1,batch_size=1,realtime_inputs=True))
        assert entered.wait(3)
        with service.db() as db:put_source(db,case.items[1]['source_url'],source())
        if interruption=='user_pause':service.pause(run['id'])
        release.set();result=case.done(service,run['id'])
        assert result['status']=='paused'
        assert result['metrics']['total']==2
        if interruption=='engine_error':assert result['metrics']['verified']==0
        else:
            assert result['metrics']['verified']==1 and len(case.calls)==2
            assert all(len(json.loads(p.split('DATA:\n')[1]))==1 for p in case.calls)
        with service.db() as db:
            assert can_carry(db,result)==(interruption=='none')
            row=db.execute('SELECT status,attempts FROM bulk_baseline_documents WHERE run_id=? AND position=1',(run['id'],)).fetchone()
            assert tuple(row)==('pending',0)
    finally:release.set()


def test_source_handoff_does_not_change_manual_frozen_runs(case):
    case,service=case;launch=service._launch
    def arrived(run):
        with service.db() as db:put_source(db,case.items[0]['source_url'],source())
        launch(run)
    with patch.object(service,'_launch',side_effect=arrived):
        result=case.done(service,service.start(dict(workers=1,batch_size=2,realtime_inputs=False))['id'])
    assert result['status']=='complete' and len(case.calls)==2
    with service.db() as db:assert not db.execute("SELECT 1 FROM bulk_baseline_events WHERE stage='input_handoff'").fetchone()
