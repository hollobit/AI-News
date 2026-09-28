import hashlib
import json
import pytest
from public_data import bucket, data_files, write_data


def fixtures():
    news=[dict(id=str(i),title=f'News {i}',day='2026-09-29',topic='medical',url=f'https://example.org/{i}',
               analyses=[dict(kind='기본 분석',text=f'Unique detail {i}',uncertainty='Unknown')]) for i in range(201)]
    graph=dict(nodes=[dict(id='source:news:'+str(i),type='source',title=n['title'],news_id=n['id'],url=n['url']) for i,n in enumerate(news)],
        edges=[dict(id='edge',source='source:news:0',target='source:news:200',layer='provenance')],pages=[],issues=[],coverage={},method='Reviewed',exported_at='now')
    return dict(news=news,papers=[],risks=[],risk_graph={},coverage={},exported_at='now'),graph


def test_complete_index_details_and_incident_edges_are_preserved(tmp_path):
    corpus,graph=fixtures();m=write_data(tmp_path,corpus,graph)
    read=lambda name:json.loads((tmp_path/name).read_text())
    assert len(read(m['news']['index']))==201
    assert read(m['news']['search'])['200'].endswith('Unique detail 200 Unknown')
    assert read(m['news']['parts']['2'])==corpus['news'][200:]
    for sid in ('source:news:0','source:news:200'):
        part=read(m['graph']['parts'][bucket(sid)])
        assert graph['edges'][0] in part['edges']
        assert any(n['id']==sid for n in part['nodes'])
    assert read(m['graph']['lookup'])['news']['200']=='source:news:200'
    for name in data_files(tmp_path):
        assert name[12:-5]==hashlib.sha256((tmp_path/name).read_bytes()).hexdigest()


def test_immutable_generation_preserves_previous_and_does_not_depend_on_time(tmp_path):
    corpus,graph=fixtures();a=write_data(tmp_path,corpus,graph)
    corpus['exported_at']='later';graph['exported_at']='later'
    b=write_data(tmp_path,corpus,graph)
    assert a['files']==b['files'] and a['version']==b['version']
    corpus['news'][0]['analyses'][0]['text']='Updated'
    c=write_data(tmp_path,corpus,graph)
    assert c['version']!=b['version']
    assert all((tmp_path/n).exists() for n in b['files'])
    assert set(c['previous_files'])==set(b['files'])-set(c['files'])


def test_manifest_rejects_traversal(tmp_path):
    (tmp_path/'site-manifest.json').write_text(json.dumps({'files':['../private.json']}))
    with pytest.raises(ValueError):data_files(tmp_path)


def test_duplicate_ids_and_missing_graph_endpoints_are_rejected(tmp_path):
    corpus, graph = fixtures()
    corpus['news'].append(corpus['news'][0])
    with pytest.raises(ValueError, match='Duplicate'): write_data(tmp_path, corpus, graph)
    corpus['news'].pop()
    graph['edges'][0]['target'] = 'missing'
    with pytest.raises(ValueError, match='endpoint'): write_data(tmp_path, corpus, graph)
