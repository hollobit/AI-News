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
    assert read(m['news']['search'])['200'].endswith('Unique detail 200\nUnknown')
    assert corpus['news'][200] in read(m['news']['parts'][bucket('200',10)])
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
    assert set(b['files']) <= set(data_files(tmp_path))
    assert c['previous_files']==[]  # Local retention ledger owns older inventories.


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


def test_multiple_generations_survive_a_day_then_expire(tmp_path):
    corpus, graph = fixtures()
    a = write_data(tmp_path, corpus, graph, now=1000)
    old_part = a['news']['parts'][bucket('0',10)]
    for minute in (1, 2, 3):
        corpus['news'][0]['analyses'][0]['text'] = str(minute)
        current = write_data(tmp_path, corpus, graph, now=1000 + 60 * minute)
    assert old_part in data_files(tmp_path)
    assert (tmp_path / old_part).exists()
    corpus['news'][0]['analyses'][0]['text'] = 'next day'
    current = write_data(tmp_path, corpus, graph, now=1000 + 86400 + 200)
    assert old_part not in data_files(tmp_path)
    assert not (tmp_path / old_part).exists()


def test_neighbor_labels_and_order_allow_paging_before_hydration(tmp_path):
    corpus, graph = fixtures()
    m = write_data(tmp_path, corpus, graph)
    part = json.loads((tmp_path / m['graph']['parts'][bucket('source:news:0')]).read_text())
    neighbor = part['neighbors']['source:news:200']
    assert neighbor == dict(id='source:news:200', title='News 200', type='source')
    assert 'analyses' not in neighbor


def test_insert_reorder_edit_delete_keep_unaffected_details_stable(tmp_path):
    corpus,graph=fixtures()
    a=write_data(tmp_path,corpus,graph)
    fresh=dict(corpus['news'][0],id='new',title='New',analyses=[])
    corpus['news'].insert(0,fresh)
    graph['nodes'].insert(0,dict(id='new-node',type='source',title='New'))
    b=write_data(tmp_path,corpus,graph)
    assert sum(a['news']['parts'].get(k)!=v for k,v in b['news']['parts'].items())==1
    assert sum(a['graph']['parts'].get(k)!=v for k,v in b['graph']['parts'].items())==1
    corpus['news'].reverse();graph['nodes'].reverse()
    c=write_data(tmp_path,corpus,graph)
    assert c['news']['parts']==b['news']['parts'] and c['graph']['parts']==b['graph']['parts']
    assert c['graph']['order']!=b['graph']['order']
    corpus['news']=[n for n in corpus['news'] if n['id']!='new']
    graph['nodes']=[n for n in graph['nodes'] if n['id']!='new-node']
    d=write_data(tmp_path,corpus,graph)
    assert d['news']['parts']==a['news']['parts'] and d['graph']['parts']==a['graph']['parts']


def test_retention_budget_keeps_current_and_newest_complete_generation(tmp_path, monkeypatch):
    import public_data
    for name in ('current', 'recent', 'old'):
        (tmp_path / name).write_bytes(b'12345')
    generations = [dict(expires_at=1, files=['old', 'current']),
                   dict(expires_at=2, files=['recent', 'current'])]
    monkeypatch.setattr(public_data, 'RETENTION_BYTES', 10)
    assert public_data.bounded_generations(tmp_path, {'current'}, generations) == generations[1:]
    monkeypatch.setattr(public_data, 'RETENTION_BYTES', 1)
    assert public_data.bounded_generations(tmp_path, {'current'}, generations) == []
    assert (tmp_path / 'current').exists()
