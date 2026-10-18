import json
import pytest
import sync_wiki_pages as publish


def test_submission_record_is_kept_even_when_inputs_change_during_publish(tmp_path,monkeypatch):
    import sqlite3
    from publication_state import result_path,state_path
    path=tmp_path/'db';output=tmp_path/'site'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE articles(title TEXT)')
    commit='a'*40
    def change(*args):
        with sqlite3.connect(path) as db:db.execute("INSERT INTO articles VALUES('new input during export')")
        return dict(status='published',commit=commit)
    monkeypatch.setattr(publish,'_sync',change)
    publish.sync(path,output)
    result=json.loads(result_path(output).read_text())
    assert result['commit']==commit and result['submitted_at'] and not state_path(output).exists()
    monkeypatch.setattr(publish,'_sync',lambda *a:dict(status='unchanged',commit=commit))
    publish.sync(path,output)
    assert json.loads(result_path(output).read_text())['submitted_at']==result['submitted_at']

def test_fingerprint_ignores_only_export_time():
    a={'knowledge.json':json.dumps({'exported_at':'one','pages':[1]}),'index.html':'site'}
    b={**a,'knowledge.json':json.dumps({'exported_at':'two','pages':[1]})}
    assert publish.fingerprint(a)==publish.fingerprint(b)
    assert publish.fingerprint(a)!=publish.fingerprint({**b,'index.html':'changed'})

def test_new_branch_has_no_master_parent(tmp_path,monkeypatch):
    for name in publish.FILES:(tmp_path/name).write_text('{}' if name.endswith('.json') else '')
    monkeypatch.setattr(publish,'export_site',lambda *args,**kwargs:{})
    calls=[]
    def api(path,method='GET',body=None):
        calls.append((path,method,body))
        return [] if path.startswith('branches') else {'sha':'test'}
    monkeypatch.setattr(publish,'api',api)
    assert publish.sync('unused',tmp_path)['status']=='published'
    assert next(body for path,_,body in calls if path=='git/commits')['parents']==[]
    assert calls[-1][2]['ref']=='refs/heads/gh-pages'
    entries=next(body for path,_,body in calls if path=='git/trees')['tree']
    assert all('sha' in entry and 'content' not in entry for entry in entries)
    assert sum(path=='git/blobs' for path,_,_ in calls)==len(publish.FILES)

def test_unexpected_remote_files_are_preserved(tmp_path,monkeypatch):
    for name in publish.FILES:(tmp_path/name).write_text('{}')
    monkeypatch.setattr(publish,'export_site',lambda *args,**kwargs:{})
    def api(path,method='GET',body=None):
        assert method=='GET'
        if path.startswith('branches'):return [{'name':'gh-pages','commit':{'sha':'head'}}]
        if path.startswith('git/commits'):return {'tree':{'sha':'tree'}}
        return {'tree':[{'path':'user-file'}]}
    monkeypatch.setattr(publish,'api',api)
    with pytest.raises(RuntimeError,match='Unexpected files'):publish.sync('unused',tmp_path)


def test_only_immutable_objects_have_bounded_transient_retries(tmp_path, monkeypatch):
    for name in publish.FILES:
        (tmp_path/name).write_text('{}' if name.endswith('.json') else '')
    monkeypatch.setattr(publish, 'export_site', lambda *args, **kwargs: {})
    monkeypatch.setattr(publish.time, 'sleep', lambda _: None)
    calls = []
    failed = set()
    def api(path, method='GET', body=None):
        calls.append(path)
        if path.startswith('branches'): return []
        if path in {'git/blobs', 'git/trees'} and path not in failed:
            failed.add(path)
            raise publish.GitHubAPIError('HTTP 502', retryable=True)
        return {'sha': 'test'}
    monkeypatch.setattr(publish, 'api', api)
    assert publish.sync('unused', tmp_path)['status'] == 'published'
    assert calls.count('git/blobs') == len(publish.FILES) + 1
    assert calls.count('git/trees') == 2
    assert calls.count('git/commits') == 1
    assert calls.count('git/refs') == 1


def test_preflight_skips_export_only_for_matching_remote_head(tmp_path,monkeypatch):
    import sqlite3
    path=tmp_path/'news.sqlite'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE articles(title TEXT)')
    output=tmp_path/'site';output.mkdir()
    calls=[]
    monkeypatch.setattr(publish,'_sync',lambda *a:(calls.append('export') or {'status':'published','commit':'head'}))
    monkeypatch.setattr(publish,'api',lambda *a,**kw:{'commit':{'sha':'head'}})
    assert publish.sync(path,output)['status']=='published'
    assert publish.sync(path,output)['preflight'] is True
    assert calls==['export']
    with sqlite3.connect(path) as db:db.execute("INSERT INTO articles VALUES('new')")
    publish.sync(path,output)
    assert calls==['export','export']
    monkeypatch.setattr(publish,'api',lambda *a,**kw:{'commit':{'sha':'other'}})
    publish.sync(path,output)
    assert len(calls)==3


def test_preflight_republishes_rotated_remote_origin_without_data_changes(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / 'news.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE articles(title TEXT)')
    output = tmp_path / 'site'
    output.mkdir()
    config = tmp_path / '.runtime/remote-access/public.json'
    config.parent.mkdir(parents=True)
    monkeypatch.setattr(publish, '__file__', str(tmp_path / 'sync_wiki_pages.py'))
    monkeypatch.setattr(publish, 'api', lambda *a, **kw: {'commit': {'sha': 'head'}})
    calls = []
    monkeypatch.setattr(publish, '_sync', lambda *a: (calls.append('export') or {'status': 'published', 'commit': 'head'}))

    config.write_text(json.dumps({'origin': 'https://first.trycloudflare.com'}))
    assert publish.sync(path, output)['status'] == 'published'
    assert publish.sync(path, output)['preflight'] is True
    config.write_text(json.dumps({'origin': 'https://second.trycloudflare.com'}))
    assert publish.sync(path, output)['status'] == 'published'
    config.write_text(json.dumps({'origin': 'https://second.trycloudflare.com', 'password': 'private', 'pid': 123}))
    assert publish.sync(path, output)['preflight'] is True
    config.unlink()
    assert publish.sync(path, output)['status'] == 'published'
    config.write_text(json.dumps({'origin': 'https://user:private@example.org'}))
    assert publish.sync(path, output)['preflight'] is True
    assert len(calls) == 3
