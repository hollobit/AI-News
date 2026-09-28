import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import patch
import pytest
from app import connect
from source_enrichment import SourceService
from graph_rag import load_integrated_graph,build_retrieval
from agent_reach_service import AgentReachService


def test_public_read_is_searchable_but_not_verified_claim(tmp_path):
    path=tmp_path/'news.sqlite3'
    with connect(path):pass
    service=SourceService(path,fetcher=lambda url:{'status':'fetched','text':'QuasarFlux controls AI access with independent review.',
        'title':'QuasarFlux research','reader':'github-public-api','platform':'github','evidence_scope':'repository_readme'})
    try:
        result=service.fetch('https://github.com/example/quasar')
        assert result['reader']=='github-public-api'
        with connect(path) as db:graph=load_integrated_graph(db,{},for_retrieval=True)
        evidence=graph.full_evidence
        assert len(evidence)==1 and evidence[0]['verification']=='unreviewed_source'
        assert evidence[0]['evidence_scope']=='repository_readme'
        assert all(n['type']!='StrategicClaim' for n in graph.full_nodes)
        assert build_retrieval(graph,'QuasarFlux')['evidence']
    finally:service.close()


def test_reach_jobs_persist_success_and_explicit_failure(tmp_path):
    def invoke(request,**kwargs):
        if request['action']=='status':return {'installed':True,'channels':[]}
        return {'status':'blocked','error':'private destination'} if 'private' in request['url'] else {'status':'fetched','text':'Readable public source','reader':'test','evidence_scope':'post_text'}
    with patch('agent_reach_service.invoke',invoke):
        service=AgentReachService(tmp_path/'jobs.sqlite3')
        try:
            for url,status in [('https://example.org/public','complete'),('https://example.org/private','failed')]:
                job=service.submit(url)
                deadline=time.monotonic()+3
                while service.get(job['id'])['status'] in {'queued','running'}:
                    assert time.monotonic()<deadline;time.sleep(.01)
                final=service.get(job['id'])
                assert final['status']==status
                assert bool(final['result']['text'])==(status=='complete')
            for bad in ['file:///etc/passwd','https://user:password@example.org/','https://example.org/\n']:
                with pytest.raises(ValueError):service.submit(bad)
        finally:service.close()


def test_worker_never_forwards_private_destination():
    spec=importlib.util.spec_from_file_location('reach_worker',Path('integrations/agent-reach/worker.py'))
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    with patch.object(worker,'_validated_target',side_effect=PermissionError),patch.object(worker,'fetch_source') as fetch:
        with pytest.raises(PermissionError):worker.read('http://127.0.0.1')
        fetch.assert_not_called()
