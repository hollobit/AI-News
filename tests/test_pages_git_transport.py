from pathlib import Path
import subprocess
import pytest
from sync_wiki_pages import git_publish


def git(root, *args):
    return subprocess.check_output(['git',*args],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def repository(tmp_path):
    remote=tmp_path/'remote.git';root=tmp_path/'source';root.mkdir()
    subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    subprocess.run(['git','init','-b','news-app'],cwd=root,check=True,capture_output=True)
    git(root,'config','user.name','Test');git(root,'config','user.email','test@example.org')
    git(root,'remote','add','origin',str(remote))
    (root/'index.html').write_text('old');git(root,'add','index.html');git(root,'commit','-m','Initial public snapshot')
    parent=git(root,'rev-parse','HEAD');git(root,'push','origin','HEAD:gh-pages')
    (root/'source.py').write_text('source remains intact');git(root,'add','source.py');git(root,'commit','-m','Source work')
    return root,remote,parent


def test_atomic_git_publication_keeps_source_checkout_and_uses_existing_parent(repository):
    root,remote,parent=repository
    head=git(root,'rev-parse','HEAD')
    commit=git_publish({'index.html':'new public snapshot','knowledge.json':'{"nodes":[]}'},parent,root=root,expected_remote=str(remote))
    assert git(remote,'rev-parse','gh-pages')==commit
    assert git(root,'rev-parse',commit+'^')==parent
    assert git(remote,'show','gh-pages:index.html')=='new public snapshot'
    assert git(root,'rev-parse','HEAD')==head and git(root,'branch','--show-current')=='news-app'
    assert (root/'source.py').read_text()=='source remains intact'
    assert git(root,'status','--porcelain')==''


def test_non_fast_forward_publication_refuses_to_overwrite_newer_snapshot(repository):
    root,remote,parent=repository
    first=git_publish({'index.html':'first'},parent,root=root,expected_remote=str(remote))
    with pytest.raises(RuntimeError,match='push'):
        git_publish({'index.html':'stale writer'},parent,root=root,expected_remote=str(remote))
    assert git(remote,'rev-parse','gh-pages')==first
    with pytest.raises(RuntimeError,match='remote'):
        git_publish({'index.html':'wrong repo'},first,root=root,expected_remote='https://wrong.example/repo')
