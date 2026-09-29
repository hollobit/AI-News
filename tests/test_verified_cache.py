import sqlite3
from unittest.mock import patch
from projection_cache import cached_read,read_projections
from verified_cache import session
import projection_cache


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
