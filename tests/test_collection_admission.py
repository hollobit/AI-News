import json
from datetime import datetime,timezone
from unittest.mock import patch

import pytest

from collection_admission import admission
from test_scheduled_collection import db,run
from test_scheduled_risks import path,run as risk_run
from corpus_completion import connect


def stored_states():
    return {'collector_last_success':'2026-09-28T10:01:00+00:00',
        'collector_corpus_snapshot':json.dumps(dict(status='complete',
            checked_at='2026-09-28T10:00:00+00:00',extracted_at='2026-09-28T10:00:01+00:00',
            total_unique=1))}


STAMP=datetime(2026,9,28,12,tzinfo=timezone.utc).timestamp()


def test_completed_stored_input_remains_available_without_faking_collection():
    result=admission(stored_states(),STAMP)
    assert result==dict(input_mode='stored_extraction',collector_current=False,
                       input_extracted_at='2026-09-28T10:00:01+00:00')


@pytest.mark.parametrize('field,value',[
    ('status','extracting'),('checked_at',None),('checked_at','invalid'),
    ('checked_at','2026-09-28T10:00:00'),('extracted_at','2026-09-29T10:00:00+00:00'),
    ('extracted_at','2026-09-28T09:00:00+00:00'),('total_unique',None),('total_unique',True),
])
def test_stale_or_partial_metadata_cannot_authorize_stored_work(field,value):
    states=stored_states();snapshot=json.loads(states['collector_corpus_snapshot'])
    snapshot[field]=value;states['collector_corpus_snapshot']=json.dumps(snapshot)
    assert admission(states,STAMP)['stage']=='waiting_for_collector'


def test_stored_baseline_uses_current_hash_checks_and_preserves_explicit_pause(db):
    for k,v in stored_states().items():db.execute('UPDATE state SET value=? WHERE key=?',(v,k))
    calls=[]
    def request(path,payload):
        calls.append(path);return {'run':dict(id='new',status='running',metrics={})}
    with patch('scheduled_collection.all_corpus_items',return_value=[dict(title='stored',text='article',source_url='https://example.org/a')]):
        result=run(db,request=request)
        assert result['stage']=='baseline_started' and result['input_mode']=='stored_extraction'
        assert not result['collector_current'] and calls==['/api/baseline']
        db.execute("INSERT INTO bulk_baseline_runs VALUES ('paused','paused',NULL,'2026-09-28','2026-09-28T12:00:00+00:00')")
        assert run(db,request=lambda *a:pytest.fail('Explicit pause'))['stage']=='baseline_attention_required'


def test_stored_deep_work_is_independent_of_telegram_but_keeps_pause_and_owner(path):
    with connect(path) as db:
        states={'collector_last_success':datetime.fromtimestamp(500,timezone.utc).isoformat(),
                'collector_corpus_snapshot':json.dumps(dict(status='complete',total_unique=1,
                    checked_at=datetime.fromtimestamp(450,timezone.utc).isoformat(),
                    extracted_at=datetime.fromtimestamp(451,timezone.utc).isoformat()))}
        for k,v in states.items():db.execute('UPDATE state SET value=? WHERE key=?',(v,k))
    assert risk_run(path,enable=True)['stage']=='user_paused'
    result=risk_run(path,resume=True)
    assert result['stage']=='started' and result['input_mode']=='stored_extraction'
    assert not result['collector_current']
    assert risk_run(path)['stage']=='running'
