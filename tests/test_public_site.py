import json
import pytest
from public_site import observation,PUBLIC_FILES,published_files
from export_wiki_site import export_site
from sync_wiki_pages import validate_static_dependencies
from test_knowledge_wiki import setup,Runner
from knowledge_wiki import KnowledgeWiki

def test_observation_excludes_raw_and_operations():
    data={'selection_version':2,'days':['2026-09-19'],'nodes':[],'edges':[],
          'evidence':{'e':dict(id='e',day='2026-09-19',title='Title',url='https://example.org/?secret=x',excerpt='PRIVATE',document_id='secret',item_id='private')},
          'article_nodes':{'private':'PRIVATE'},'timing':{'internal':'PRIVATE'}}
    result=observation({'data':data})
    assert 'PRIVATE' not in json.dumps(result)
    assert result['evidence']['e']['url']=='https://example.org/'
    assert 'excerpt' not in result['evidence']['e']
    assert observation({})=={'unavailable':True}

def test_complete_public_documents_keep_membership_without_excerpts():
    raw={'data':dict(selection_version=2,document_index_version=1,days=['2026-09-19'],nodes=[dict(id='n',document_ids_by_day=[['a']])],edges=[],evidence={},documents={'a':dict(id='a',document_id='private-id',title='기사',day='2026-09-19',url='https://example.org/a?token=secret',excerpt='SECRET')})}
    result=observation(raw)
    assert result['nodes'][0]['document_ids_by_day']==[['a']]
    assert result['documents']['a']['url']=='https://example.org/a'
    assert 'SECRET' not in json.dumps(result) and 'private-id' not in json.dumps(result)

def test_full_export_manifest_and_relative_assets(tmp_path):
    path=tmp_path/'news.db';setup(path)
    service=KnowledgeWiki(path,Runner(),enabled=True,start_worker=False)
    service.compile('medical');service.close()
    target=tmp_path/'site'
    export_site(path,target,full_site=True)
    validate_static_dependencies(target)
    assert {p.name for p in target.iterdir()}==set(published_files(target))
    assert 'public.js' in (target/'index.html').read_text()
    assert 'data-mode="static"' in (target/'knowledge.html').read_text()
    html=(target/'observatory.html').read_text()
    assert 'src="/' not in html and 'href="/' not in html
    assert 'paper-context.js' not in html
    assert json.loads((target/'site.json').read_text())['news']==[]
    export_site(path,target,full_site=True)


def test_publish_rejects_missing_module_dependency(tmp_path):
    (tmp_path/'knowledge.html').write_text('<script type="module" src="./atlas.js"></script>')
    with pytest.raises(RuntimeError,match='missing published asset atlas.js'):
        validate_static_dependencies(tmp_path,('knowledge.html',))
    (tmp_path/'atlas.js').write_text("import * as THREE from './three.module.js';")
    with pytest.raises(RuntimeError,match='missing published asset three.module.js'):
        validate_static_dependencies(tmp_path,('knowledge.html','atlas.js'))

@pytest.mark.parametrize('url', [
    'https://news.example/read?id=123&section=it&page=2#detail',
    'https://news.example/article?code=ABC123',
    'https://news.example/a(clean).pdf?x=%2f&x=+&flag&empty=',
    'https://news.example/?redirect=https%3A%2F%2Fother.example%2Fa%3Fid%3D5%26page%3D2',
])
def test_public_source_keeps_semantic_parameters(url):
    from export_wiki_site import public_url
    assert public_url(url) == url


def test_public_source_hides_credentials_without_losing_article_id():
    from export_wiki_site import public_url
    assert public_url('https://news.example/?id=123&access_token=PRIVATE') == 'https://news.example/?id=123'
    assert public_url('https://news.example/?id=123#access_token=PRIVATE') == 'https://news.example/?id=123'
    assert public_url('https://news.example/?id=123&next=https%3A%2F%2Fother.example%2F%3Ftoken%3DPRIVATE') == 'https://news.example/?id=123'


def test_public_source_resolves_proven_legacy_repair():
    import sqlite3
    from export_wiki_site import public_url
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE repaired_source_links(old_url TEXT, new_url TEXT)')
    old = 'https://news.example/a(clean'
    new = old + ').pdf?id=1&part=2'
    db.execute('INSERT INTO repaired_source_links VALUES(?,?)', (old, new))
    assert public_url(old, db) == new
    db.close()
