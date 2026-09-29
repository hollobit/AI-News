import json
from pathlib import Path
import pytest
from export_wiki_site import export_site
from test_knowledge_wiki import setup


def test_failed_build_keeps_last_good_and_next_export_recovers(tmp_path,monkeypatch):
    path=tmp_path/'news.db';setup(path)
    target=tmp_path/'site'
    first=export_site(path,target,full_site=True)
    previous=target.resolve();manifest=(target/'site-manifest.json').read_bytes()
    import public_data
    write=public_data.write_data
    def interrupted(root,*args,**kwargs):
        (root/'site-manifest.json.tmp').write_text('interrupted')
        raise RuntimeError('Injected failure')
    monkeypatch.setattr(public_data,'write_data',interrupted)
    with pytest.raises(RuntimeError,match='Injected'):export_site(path,target,full_site=True)
    assert target.resolve()==previous and (target/'site-manifest.json').read_bytes()==manifest
    monkeypatch.setattr(public_data,'write_data',write)
    export_site(path,target,full_site=True)
    assert target.resolve()!=previous
    assert json.loads((target/'build.json').read_text())['assets']['public-data.js']


def test_legacy_reserved_temporary_is_recoverable_but_foreign_files_rejected(tmp_path):
    path=tmp_path/'news.db';setup(path)
    target=tmp_path/'site';target.mkdir()
    (target/'knowledge.json').write_text('{}');(target/'site-manifest.json.tmp').write_text('partial')
    export_site(path,target,full_site=True)
    assert not (target/'site-manifest.json.tmp').exists()
    (target/'personal.txt').write_text('do not delete')
    with pytest.raises(ValueError):export_site(path,target,full_site=True)
    assert (target/'personal.txt').read_text()=='do not delete'


def test_legacy_pointer_gap_recovers_and_external_symlinks_rejected(tmp_path):
    path=tmp_path/'news.db';setup(path)
    target=tmp_path/'site';export_site(path,target,full_site=True)
    target.unlink() # crash after moving legacy directory, before pointer switch
    export_site(path,target,full_site=True)
    assert (target/'knowledge.json').exists()
    outside=tmp_path/'outside';outside.mkdir()
    target.unlink();target.symlink_to(outside)
    with pytest.raises(ValueError,match='symlink'):export_site(path,target,full_site=True)
    assert list(outside.iterdir())==[]
