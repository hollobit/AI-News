import json
import sqlite3
from code_policy import digest
from completion_reconciliation import report_summaries
from publication_state import signature
from export_wiki_site import public_url


def test_display_policy_does_not_invalidate_evidence(tmp_path):
    for name in ('source_titles','news_repository','strategic_workflow'):
        (tmp_path/(name+'.py')).write_text('old')
    before={s:digest(s,tmp_path) for s in ('source','validation','publication')}
    (tmp_path/'source_titles.py').write_text('display edit')
    assert digest('source',tmp_path)==before['source']
    assert digest('validation',tmp_path)==before['validation']
    assert digest('publication',tmp_path)!=before['publication']
    (tmp_path/'strategic_workflow.py').write_text('review edit')
    assert digest('validation',tmp_path)!=before['validation']
    (tmp_path/'news_repository.py').write_text('# input edit')
    assert digest('source',tmp_path)!=before['source']


def test_report_digest_reuse_and_revocation():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE strategic_workflow_runs(id TEXT,status TEXT,error TEXT);
        CREATE TABLE strategic_workflow_artifacts(run_id TEXT,stage TEXT,payload_json TEXT);
        CREATE TABLE corpus_completion_documents(cycle_id TEXT,status TEXT,workflow_run_id TEXT);
        INSERT INTO strategic_workflow_runs VALUES('r','complete',NULL);
        INSERT INTO strategic_workflow_artifacts VALUES('r','final','{"review":"accepted"}');
        INSERT INTO corpus_completion_documents VALUES('c','complete','r');''')
    first=report_summaries(db,'c')
    statements=[];db.set_trace_callback(statements.append)
    assert report_summaries(db,'c')==first
    assert not any(s.startswith('SELECT payload_json') for s in statements)
    db.execute("UPDATE strategic_workflow_artifacts SET payload_json='{}'")
    changed=report_summaries(db,'c')
    assert changed['r']['digest']!=first['r']['digest']
    db.execute("UPDATE strategic_workflow_runs SET error='review revoked'")
    assert report_summaries(db,'c')['r']['error']=='review revoked'
    db.execute('DELETE FROM strategic_workflow_artifacts')
    assert report_summaries(db,'c')['r']['digest']!=changed['r']['digest']


def test_publication_signature_tracks_data_and_assets(tmp_path):
    path=tmp_path/'db.sqlite'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE news(text TEXT)')
    (tmp_path/'static').mkdir()
    before=signature(path,tmp_path)
    assert signature(path,tmp_path)==before
    with sqlite3.connect(path) as db:db.execute("INSERT INTO news VALUES('new evidence')")
    changed=signature(path,tmp_path);assert changed!=before
    (tmp_path/'static'/'index.html').write_text('new UI')
    assert signature(path,tmp_path)!=changed


def test_article_key_parameter_survives_publication():
    assert public_url('https://example.org/article?key=42&part=2')=='https://example.org/article?key=42&part=2'
    assert 'api_key=' not in public_url('https://example.org/article?key=42&api_key=secret')


def test_publication_ignores_internal_cache_and_collector_heartbeat(tmp_path):
    path=tmp_path/'db.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE completion_report_digests(run_id TEXT,digest TEXT)')
    before=signature(path,tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO state VALUES('collector_last_success','now')")
        db.execute("INSERT INTO completion_report_digests VALUES('run','hash')")
    assert signature(path,tmp_path)==before
    with sqlite3.connect(path) as db:db.execute("INSERT INTO state VALUES('demo','1')")
    assert signature(path,tmp_path)!=before
