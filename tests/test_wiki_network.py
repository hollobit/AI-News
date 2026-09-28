import io
import json
import re
import sqlite3
import zipfile
import pytest
from test_knowledge_wiki import setup, Runner
from knowledge_wiki import KnowledgeWiki, read_wiki
from wiki_network import project, read_network, export_vault
from export_wiki_site import snapshot, export_site, public_url
from wiki_structure import validate


@pytest.fixture
def service(tmp_path):
    path=tmp_path/'news.db';setup(path)
    value=KnowledgeWiki(path,Runner(),enabled=True,start_worker=False)
    assert value.compile('medical')=='complete'
    yield value
    value.close()


def test_source_claim_identity_and_layer_separation(service):
    service.manage(dict(topic='clinical',name='임상',terms=['의료']))
    service.compile('clinical')
    with service.db() as db:
        graph=project(read_wiki(db,include_details=True))
        assert sum(n['type']=='source' for n in graph['nodes'])==1
        assert sum(n['type']=='claim' for n in graph['nodes'])==1
        assert {e['layer'] for e in graph['edges']}=={'semantic','provenance','recommendation'}
        assert all(e['source_ids'] for e in graph['edges'])
        assert all(e['kind']=='shared_source' for e in graph['edges'] if e['layer']=='recommendation')
        view=read_network(db,identity='medical',layer='semantic')
        assert view['detail']['title']=='의료'
        assert all(e['layer']=='semantic' for e in view['edges'])
        semantic=read_network(db,layer='semantic')
        assert all(n['type'] not in ('source','claim') for n in semantic['nodes'])
        assert semantic['total'] < read_network(db)['total']
        source=read_network(db,source_url='https://example.org/medical')
        assert source['detail']['type']=='source'
        assert source['detail']['connections']


def test_stale_never_leaks_to_network_or_exports(service):
    with service.db() as db:db.execute("UPDATE articles SET text='정정된 원문'")
    with service.db() as db:
        network=read_network(db)
        assert network['nodes']==[]
        assert any(i['kind']=='stale' for i in network['issues'])
        assert snapshot(db)['nodes']==[]
        archive=zipfile.ZipFile(io.BytesIO(export_vault(db)))
        assert not any(n.startswith('raw/sources/') for n in archive.namelist())


def test_vault_safe_paths_and_resolved_wikilinks(service):
    with service.db() as db:data=export_vault(db)
    archive=zipfile.ZipFile(io.BytesIO(data))
    names=set(archive.namelist())
    assert {'purpose.md','schema.md','wiki/index.md','wiki/log.md','manifest.json'}<=names
    for name in names:
        assert '..' not in name and not name.startswith('/')
        markdown=re.sub(r'`[^`]*`','',archive.read(name).decode())
        for target in re.findall(r'\[\[([^\]]+)\]\]',markdown):
            assert target+'.md' in names
    assert len([n for n in names if n.startswith('raw/sources/')])==1


def test_static_publication_allowlist_and_repeat_export(service,tmp_path):
    with service.db() as db:
        public=snapshot(db)
        assert 'dependency' not in json.dumps(public)
        assert all('excerpt' not in n and 'href' not in n for n in public['nodes'])
        assert any('excerpt' in n for n in snapshot(db,True)['nodes'])
    output=tmp_path/'site'
    assert export_site(service.path,output)['pages']==2
    assert export_site(service.path,output)['pages']==2
    assert 'data-mode="static"' in (output/'index.html').read_text()
    (output/'private.db').touch()
    with pytest.raises(ValueError):export_site(service.path,output)


@pytest.mark.parametrize('url,expected',[
    ('javascript:alert(1)',''),('https://user:password@example.org/a',''),
    ('http://127.0.0.1/private',''),('https://example.org/a?token=secret#private','https://example.org/a')])
def test_public_urls(url,expected):assert public_url(url)==expected


def test_structured_notes_require_exact_original_quote():
    bundle={'evidence':[{'id':'a','text':'실제 원문입니다.'}]}
    result={'notes':[dict(type='claim',label='주장',evidence_id='a',quote='실제 원문')]}
    validate(result,bundle)
    result['notes'][0]['quote']='발명한 내용'
    with pytest.raises(ValueError):validate(result,bundle)


def test_question_archive_rechecks_raw_sources(service):
    answer=dict(claims=[dict(text='기존 설명',evidence_ids=['e'])],
        evidence=[dict(id='e',source_url='https://example.org/medical')],
        verification={'method':'independent_evidence_review'})
    result=service.archive_question('job','의료 AI 연구는 무엇인가?',answer)
    assert service.compile(result['topic'])=='complete'
    page=service.get(result['topic'])['page']
    assert page['kind']=='question'
    assert '기존 설명' not in str(page['claims'])
    with service.db() as db:db.execute('DELETE FROM articles')
    assert service.get(result['topic'])['page'] is None
    with pytest.raises(ValueError):service.archive_question('job','다른 질문',dict(answer,verification={}))


def test_structure_cache_reused_on_manual_review_retry(service):
    calls=service.runner.calls.count('wiki_structure')
    service.request('medical');service.compile('medical')
    assert service.runner.calls.count('wiki_structure')==calls


def test_purpose_change_invalidates_only_policy_bound_editions(service):
    from unittest.mock import patch
    with patch('wiki_structure.purpose',return_value='변경된 사용자 목적'):
        assert service.get('medical')['page'] is None
        assert service.compile('medical')=='complete'
        assert service.get('medical')['page']


def test_explicit_identity_merge_and_unmerge_preserve_claims(service):
    from copy import deepcopy
    with service.db() as db:view=read_wiki(db,include_details=True)
    child=next(p for p in view['pages'] if p['kind']=='event')
    child['kind']='entity'
    second=deepcopy(child);second.update(id='clinical:second',topic='clinical')
    view['pages'].append(second)
    before=project(view)
    assert before['page_ids'][child['id']]!=before['page_ids'][second['id']]
    view['aliases']=[dict(source=child['id'],target=second['id'],reason='explicit')]
    after=project(view)
    assert after['page_ids'][child['id']]==after['page_ids'][second['id']]
    assert {n['id'] for n in before['nodes'] if n['type']=='claim'}=={n['id'] for n in after['nodes'] if n['type']=='claim'}
    view['aliases']=[]
    assert project(view)['page_ids']==before['page_ids']


def test_static_claim_citations_resolve_to_shared_source_nodes(service):
    with service.db() as db:value=snapshot(db)
    ids={n['id'] for n in value['nodes'] if n['type']=='source'}
    assert all(set(c['evidence_ids'])<=ids for p in value['pages'] for c in p['claims'])
