import sqlite3
import pytest
from unittest.mock import patch
from projection_cache import cached_read,read_projections
from verified_cache import session
import projection_cache
import verified_cache


def test_persistent_verification_reuse_and_exact_key_policy_invalidation(tmp_path):
    path=tmp_path/'news.db';calls=[]
    def build():calls.append(1);return {'accepted':True,'evidence':['current']}
    with sqlite3.connect(path) as db:
        a=cached_read(db,'baseline_verified_content','input1',build,copy_result=False)
        a['evidence'].clear()
    with sqlite3.connect(path) as db,session(db):
        b=cached_read(db,'baseline_verified_content','input1',build,copy_result=False)
        assert b['evidence']==['current'] and len(calls)==1
        cached_read(db,'baseline_verified_content','input2',build)
        with patch('verified_cache.policy',return_value='changed'):
            cached_read(db,'baseline_verified_content','input1',build)
    assert len(calls)==3


def test_http_cache_miss_never_creates_validation_store(tmp_path):
    path=tmp_path/'news.db'
    with sqlite3.connect(path) as db,read_projections():
        assert cached_read(db,'paper_verified_content','new',lambda:{'accepted':False})=={'accepted':False}
    assert not (tmp_path/'news.db.validated.sqlite3').exists()


def test_oversized_graph_spills_as_json_and_restores_full_retrieval(tmp_path):
    from graph_rag import GraphResult
    path=tmp_path/'news.db';calls=[]
    def build():
        calls.append(1)
        return GraphResult({'nodes':[{'id':'visible'}],'edges':[],'evidence':[]},
            full_nodes=[{'id':'visible'},{'id':'hidden'}],full_edges=[],full_evidence=[])
    with sqlite3.connect(path) as db,patch.dict(projection_cache._BUCKET_LIMITS,graph=1):
        first=cached_read(db,'integrated_graph','v1',build,copy_result=False)
        again=cached_read(db,'integrated_graph','v1',build,copy_result=False)
        assert len(calls)==1 and again.full_nodes==first.full_nodes
        cached_read(db,'integrated_graph','v2',build,copy_result=False)
        assert len(calls)==2


def test_draining_workers_do_not_evict_another_policy_generation(tmp_path):
    path=tmp_path/'rolling.db';calls=[]
    def build():calls.append(1);return {'accepted':True}
    for policy in ('old','new','old','new'):
        with sqlite3.connect(path) as db,patch('verified_cache.policy',return_value=policy):
            cached_read(db,'baseline_verified_content','same-input',build)
    assert len(calls)==2


def test_cache_hit_does_not_scan_or_write_the_validation_store(tmp_path):
    path = tmp_path / 'news.db'
    with sqlite3.connect(path) as db:
        cached_read(db, 'baseline_verified_content', 'input', lambda: {'accepted': True})
        original = sqlite3.connect
        queries = []
        def traced(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(queries.append)
            return connection
        with patch('verified_cache.sqlite3.connect', side_effect=traced):
            with session(db):
                result = cached_read(db, 'baseline_verified_content', 'input', lambda: pytest.fail('cache miss'))
        assert result == {'accepted': True}
        assert not any('SUM(LENGTH(value))' in sql or sql.startswith(('INSERT', 'DELETE')) for sql in queries)


def test_byte_budget_applies_after_batch_commit_and_across_policies(tmp_path):
    import random
    path = tmp_path / 'news.db'
    # Each item fits alone but the 64-entry committed batch exceeds this budget.
    value = ''.join(random.Random(index).randbytes(64).hex() for index in range(4))
    with sqlite3.connect(path) as db, patch.object(verified_cache, 'MAX_BYTES', 1024):
        with session(db), patch('verified_cache.policy', return_value='old'):
            for index in range(64):
                verified_cache.read(db, 'baseline_verified_content', str(index), lambda: value)
            assert not verified_cache._LOCAL.store[1].in_transaction
        with session(db), patch('verified_cache.policy', return_value='new'):
            verified_cache.read(db, 'baseline_verified_content', 'new', lambda: value)
    with sqlite3.connect(str(path) + '.validated.sqlite3') as cache:
        assert cache.execute('SELECT SUM(LENGTH(value)) FROM validations').fetchone()[0] <= 1024
        assert cache.execute('SELECT value FROM validations WHERE key=? AND policy=?', ('new', 'new')).fetchone()
