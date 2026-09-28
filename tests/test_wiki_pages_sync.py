import json
import pytest
import sync_wiki_pages as publish

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


def test_only_immutable_blob_upload_has_bounded_transient_retries(tmp_path, monkeypatch):
    for name in publish.FILES:
        (tmp_path/name).write_text('{}' if name.endswith('.json') else '')
    monkeypatch.setattr(publish, 'export_site', lambda *args, **kwargs: {})
    monkeypatch.setattr(publish.time, 'sleep', lambda _: None)
    calls = []
    failed = False
    def api(path, method='GET', body=None):
        nonlocal failed
        calls.append(path)
        if path.startswith('branches'): return []
        if path == 'git/blobs' and not failed:
            failed = True
            raise publish.GitHubAPIError('HTTP 502', retryable=True)
        return {'sha': 'test'}
    monkeypatch.setattr(publish, 'api', api)
    assert publish.sync('unused', tmp_path)['status'] == 'published'
    assert calls.count('git/blobs') == len(publish.FILES) + 1
    assert calls.count('git/commits') == 1
    assert calls.count('git/refs') == 1
