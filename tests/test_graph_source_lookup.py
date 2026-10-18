import hashlib
import json
import sqlite3
from unittest.mock import patch

import pytest

from graph_source_lookup import CurrentSources
from graph_rag import _analysis_sources, current_workflow_graph
from link_groups import canonical_url


@pytest.mark.parametrize('state', ['changed','failed','deleted','same'])
def test_selected_workflow_originals_keep_full_current_source_gate(state):
    graph={'nodes':[{'id':'claim','type':'StrategicClaim','evidence_ids':['e']},
                    {'id':'source','type':'SourceDocument','evidence_ids':['e']}],
           'edges':[{'source':'claim','target':'source','evidence_ids':['e']}],
           'evidence':[{'id':'e','origin':'fetched_url_excerpt','source_url':'https://example.org/a',
                        'text':'확인된 사실과 조건 전체'}]}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT)')
        db.execute('INSERT INTO source_excerpts VALUES(?,?)',('https://example.org/noise',json.dumps({'text':'큰 무관 원문'*10000})))
        if state!='deleted':
            db.execute('INSERT INTO source_excerpts VALUES(?,?)',('https://example.org/a',json.dumps({
                'status':'failed' if state=='failed' else 'fetched',
                'text':'확인된 사실과 조건 전체'+(' 수정' if state=='changed' else '')})))
        decoded=[]
        def decode(body):decoded.append(body);return json.loads(body)
        lookup=CurrentSources(db,decode)
        result,removed=current_workflow_graph(graph,lookup)
        assert removed==(0 if state=='same' else 1)
        assert bool(result['edges'])==(state=='same')
        assert len(decoded)==(0 if state=='deleted' else 1)


def test_selected_external_decodes_only_requested_original_and_refreshes_metadata():
    url='https://example.org/a';identity='reach:'+hashlib.sha256(url.encode()).hexdigest()[:24]
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT)')
        db.execute('INSERT INTO source_excerpts VALUES(?,?)',('https://example.org/noise',json.dumps({'text':'무관 원문'})))
        for title,reader in [('원래 제목','reader1'),('정정된 제목','reader2')]:
            db.execute('INSERT OR REPLACE INTO source_excerpts VALUES(?,?)',(url,json.dumps({
                'status':'fetched','title':title,'reader':reader,'text':'현재 원문'})))
            with patch('graph_rag._json',wraps=json.loads) as decode:
                sources,_=_analysis_sources(db,selected={'external':{identity}})
            assert decode.call_count==1
            assert len(sources)==1
            evidence=sources[0]['result']['evidence'][0]
            assert evidence['title']==title and evidence['reader']==reader


def test_canonical_collision_precedence_and_missing_source_match_full_reader():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT)')
        db.executemany('INSERT INTO source_excerpts VALUES(?,?)',[
            ('https://example.org/a?utm_source=first',json.dumps({'text':'first'})),
            ('https://example.org/a',json.dumps({'text':'last'}))])
        expected={canonical_url(url):json.loads(body) for url,body in db.execute('SELECT canonical_url,result_json FROM source_excerpts')}
        lookup=CurrentSources(db,json.loads)
        assert dict(lookup)==expected
        assert lookup.get('https://example.org/missing') is None


def test_url_lookup_reuse_does_not_share_bodies_between_databases_or_edits():
    url='https://example.org/a'
    with sqlite3.connect(':memory:') as first,sqlite3.connect(':memory:') as second:
        for db,text in [(first,'first database'),(second,'second database')]:
            db.execute('CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT)')
            db.execute('INSERT INTO source_excerpts VALUES(?,?)',(url,json.dumps({'text':text})))
            assert CurrentSources(db,json.loads)[url]['text']==text
        first.execute('UPDATE source_excerpts SET canonical_url=?',('https://example.org/renamed',))
        lookup=CurrentSources(first,json.loads)
        assert lookup.get(url) is None
        assert lookup['https://example.org/renamed']['text']=='first database'


def test_source_body_reads_share_callers_transaction_snapshot(tmp_path):
    path=tmp_path/'sources.db';url='https://example.org/a'
    with sqlite3.connect(path) as setup:
        setup.executescript('PRAGMA journal_mode=WAL;CREATE TABLE source_excerpts(canonical_url TEXT PRIMARY KEY,result_json TEXT)')
        setup.execute('INSERT INTO source_excerpts VALUES(?,?)',(url,json.dumps({'text':'before'})))
    with sqlite3.connect(path) as reader:
        reader.execute('BEGIN');lookup=CurrentSources(reader,json.loads)
        with sqlite3.connect(path) as writer:
            writer.execute('UPDATE source_excerpts SET result_json=?',(json.dumps({'text':'after'}),))
        assert lookup[url]['text']=='before'
    with sqlite3.connect(path) as reader:assert CurrentSources(reader,json.loads)[url]['text']=='after'
