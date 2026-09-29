"""Owned immutable export generations and a recoverable atomic current pointer."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import uuid
from public_data import DATA_NAME, data_files, retention_path

_LOCAL = threading.local()
_MUTEX = threading.RLock()


def logical_path(output):
    # Resolve only the parent: the final component may be our current pointer.
    path = Path(output).absolute()
    return path.parent.resolve() / path.name


@contextmanager
def output_lock(output):
    target = logical_path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    key = str(target)
    with _MUTEX:
        held = getattr(_LOCAL, 'held', set())
        if key in held:
            yield
            return
        with target.with_name('.' + target.name + '.export.lock').open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _LOCAL.held = held | {key}
            try:
                yield
            finally:
                _LOCAL.held = held


def _json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def _pointer(target, destination, root):
    temporary = root / 'current-link'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(destination)
    temporary.replace(target)


@contextmanager
def generation(output, allowed):
    target = logical_path(output)
    root = target.with_name('.' + target.name + '.generations')
    marker = root / 'owner.json'
    if root.exists():
        if root.is_symlink() or not marker.is_file():
            raise ValueError('Unowned export generation directory')
        owner = json.loads(marker.read_text())
        if owner.get('target') != str(target):
            raise ValueError('Unexpected export generation owner')
    else:
        root.mkdir()
        owner = {'target': str(target), 'current': None}
        _json(marker, owner)
    def owned(path):
        return path.parent == root and path.name.startswith('generation-') and path.is_dir() and not path.is_symlink()
    if target.is_symlink() and not owned(target.resolve()):
        raise ValueError('Unexpected export target symlink')
    if not target.exists() and owner.get('current'):
        previous = root / owner['current']
        if not owned(previous):
            raise ValueError('Missing previous export generation')
        _pointer(target, previous, root)
    old = target.resolve() if target.exists() else None
    if old:
        names = set(allowed(old))
        # This exact reserved temporary file can remain after the old exporter
        # crashed. It is never copied or published; arbitrary files still fail.
        names.add('site-manifest.json.tmp')
        entries = list(old.iterdir())
        if any(p.is_symlink() or not p.is_file() or p.name not in names for p in entries) or (entries and not (old / 'knowledge.json').is_file()):
            raise ValueError('비어 있거나 이 도구가 만든 전용 출력 폴더를 사용하세요.')
    # Only our dedicated incomplete build directories may be discarded.
    for abandoned in root.glob('building-*'):
        if abandoned.is_dir() and not abandoned.is_symlink():
            shutil.rmtree(abandoned)
            retention_path(abandoned).unlink(missing_ok=True)
    stage = root / ('building-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        if old and (old / 'site-manifest.json').exists():
            shutil.copyfile(old / 'site-manifest.json', stage / 'site-manifest.json')
            if retention_path(old).exists():
                shutil.copyfile(retention_path(old), retention_path(stage))
            for name in data_files(old):
                if not (old / name).is_file():
                    raise ValueError('Missing retained public data')
                os.link(old / name, stage / name)
        yield stage
        for name in data_files(stage):
            if hashlib.sha256((stage / name).read_bytes()).hexdigest() != name[12:-5]:
                raise ValueError('Invalid public data content hash')
        destination = root / stage.name.replace('building-', 'generation-', 1)
        stage.rename(destination)
        if retention_path(stage).exists():
            retention_path(stage).rename(retention_path(destination))
        if old and not target.is_symlink():
            legacy = root / ('generation-legacy-' + uuid.uuid4().hex)
            # Record the recovery destination BEFORE moving the legacy folder.
            _json(marker, dict(owner, current=legacy.name))
            target.rename(legacy)
            if retention_path(old).exists():
                retention_path(old).rename(retention_path(legacy))
        _pointer(target, destination, root)
        _json(marker, dict(owner, current=destination.name))
        generations = sorted((p for p in root.glob('generation-*') if owned(p)), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = {destination, *generations[:2]}
        for prior in generations:
            if prior not in keep:
                shutil.rmtree(prior)
                retention_path(prior).unlink(missing_ok=True)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
            retention_path(stage).unlink(missing_ok=True)
