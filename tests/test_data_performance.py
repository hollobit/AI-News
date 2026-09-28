import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import morphology
from source_enrichment import SourceService
from keyword_index import init_keyword_index, ensure_keyword_index, mark_keyword_source_changed, sync_keyword_index


def test_morphology_queries_only_requested_hash_and_copy_isolation():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE morphology_cache(content_hash TEXT PRIMARY KEY, keywords_json TEXT NOT NULL)')
    db.execute("INSERT INTO morphology_cache VALUES ('unrelated','not json')")
    db.commit()
    item={'chat_id':1,'message_id':90210,'item_index':1,'title':'희소 어텐션','text':'성능'}
    calls=[]; db.set_trace_callback(calls.append)
    first=morphology.keyword_records(db,[item]); value=next(iter(first.values()))
    expected=json.loads(json.dumps(value))
    value[0]['label']='mutated'; value[0]['pos'].append('mutated')
    with patch.object(morphology,'extract_keywords',side_effect=AssertionError('cache miss')):
        second=morphology.keyword_records(db,[item])
    assert next(iter(second.values()))==expected
    selects=[sql for sql in calls if sql.startswith('SELECT content_hash')]
    assert selects and all('WHERE content_hash IN' in sql for sql in selects)


def test_morphology_same_hash_singleflight():
    started=threading.Event(); release=threading.Event(); count=[]
    def extractor(text):
        count.append(text); started.set(); release.wait(2)
        return [{'label':'test','pos':['NNP']}]
    with patch.object(morphology,'extract_keywords',extractor), ThreadPoolExecutor(2) as pool:
        first=pool.submit(morphology._decoded_keywords,'isolated-concurrent-test','unique')
        assert started.wait(2)
        second=pool.submit(morphology._decoded_keywords,'isolated-concurrent-test','unique')
        release.set()
        assert first.result()==second.result()
    assert len(count)==1


def test_source_singleflight_and_revision(tmp_path):
    path=tmp_path/'cache.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
        db.execute("INSERT INTO state VALUES('keyword_source_revision','1')")
    started=threading.Event(); release=threading.Event(); calls=[]
    response={'status':'fetched','title':'제목','text':'짧은 정상 기사'}
    def fetch(url):
        calls.append(url); started.set(); release.wait(2)
        return response
    service=SourceService(path,fetch)
    try:
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(service.fetch,'https://example.org/single')
            assert started.wait(2)
            b=pool.submit(service.fetch,'https://example.org/single')
            release.set(); assert a.result()==b.result()
        assert len(calls)==1
        def revision():
            with sqlite3.connect(path) as db:
                return db.execute("SELECT value FROM state WHERE key='keyword_source_revision'").fetchone()[0]
        assert revision()=='2'
        service.fetch('https://example.org/single',True)
        assert revision()=='2'
        response['title']='수정 제목'; service.fetch('https://example.org/single',True)
        assert revision()=='3'
        response.update(status='failed',error='HTTP 403'); service.fetch('https://example.org/single',True)
        assert revision()=='4'
        service.fetch('https://example.org/single',True)
        assert revision()=='4'
    finally:
        service.close()


def test_index_preparation_outside_transaction_and_unchanged_no_writes():
    db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
    init_keyword_index(db); db.commit()
    item={'chat_id':1,'message_id':1,'item_index':1,'title':'로봇 연구','text':'산업 로봇','day':'2026-09-15','source_url':'https://example.org/a'}
    import keyword_index
    original=keyword_index._words
    def checked(*args):
        assert not db.in_transaction
        return original(*args)
    with patch.object(keyword_index,'_words',checked):
        assert ensure_keyword_index(db,lambda:[item])
    before=db.total_changes
    sync_keyword_index(db,[item])
    assert db.total_changes==before
    db.commit()
    assert not ensure_keyword_index(db,lambda: (_ for _ in ()).throw(AssertionError('unneeded')))


def test_index_rechecks_revision_after_preparation():
    db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT)')
    init_keyword_index(db); db.commit(); calls=[]
    def factory():
        calls.append(1)
        if len(calls)==1:
            mark_keyword_source_changed(db); db.commit()
        return []
    assert ensure_keyword_index(db,factory)
    assert len(calls)==2
