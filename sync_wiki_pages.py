"""Publish only the reviewed static snapshot to the dedicated gh-pages branch."""
import argparse
import base64
import fcntl
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
from export_wiki_site import export_site
from public_site import PUBLIC_FILES, published_files
from public_data import DATA_NAME

REPO = 'hollobit/AI-News'
BRANCH = 'gh-pages'
FILES = ('index.html', 'wiki-network.js', 'wiki-network-3d.js', 'wiki-network.css', 'three.module.js', 'three.core.js', 'three.LICENSE', 'knowledge.json', '.nojekyll', 'README.md')
LEGACY_FILES=FILES
FILES=PUBLIC_FILES

class _AssetLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths=[]

    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag=='script' and values.get('src'):
            self.paths.append(values['src'])
        elif tag=='link' and 'stylesheet' in values.get('rel','').split() and values.get('href'):
            self.paths.append(values['href'])


def validate_static_dependencies(output, files=FILES):
    """Refuse to publish HTML or modules whose local assets are absent from the manifest."""
    root=Path(output)
    listed=set(files)
    for name in files:
        if name.endswith('.html'):
            parser=_AssetLinks()
            parser.feed((root/name).read_text())
            references=parser.paths
        elif name.endswith('.js'):
            references=re.findall(r'\b(?:from\s*|import\s*)[\'\"]\./([^\'\"]+)[\'\"]', (root/name).read_text())
        else:
            continue
        for reference in references:
            uri=urlsplit(reference)
            if uri.scheme or uri.netloc or uri.path.startswith('/'):
                continue
            dependency=uri.path.removeprefix('./')
            if dependency and (dependency not in listed or not (root/dependency).is_file()):
                raise RuntimeError(f'{name}: missing published asset {dependency}')

def api(path, method='GET', body=None):
    command = ['gh', 'api', f'repos/{REPO}/{path}', '--method', method]
    if body is not None:command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'GitHub API {method} {path} failed (exit {result.returncode})')
    return json.loads(result.stdout) if result.stdout.strip() else {}

def fingerprint(files):
    normalized=dict(files)
    for name in ('knowledge.json','site.json','site-manifest.json'):
        if name not in normalized:continue
        data=json.loads(normalized[name]);data.pop('exported_at',None)
        normalized[name]=json.dumps(data,sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def sync(db, output):
    summary=export_site(db,output,full_site=True)
    names=published_files(output)
    validate_static_dependencies(output,names)
    files={name:(Path(output)/name).read_text() for name in names}
    branches=api('branches?per_page=100')
    branch=next((b for b in branches if b['name']==BRANCH),None)
    parent=branch['commit']['sha'] if branch else None
    if parent:
        commit=api('git/commits/'+parent)
        tree=api('git/trees/'+commit['tree']['sha'])
        existing_paths={entry['path'] for entry in tree['tree']}
        # A previous Pages snapshot may not yet contain newly added allowlisted
        # assets. It is safe to add missing files, but never overwrite an
        # unexpected path or a tree without the expected site entry points.
        if (any(name not in set(FILES) and not DATA_NAME.fullmatch(name) for name in existing_paths)
                or not {'index.html','knowledge.json'} <= existing_paths):
            raise RuntimeError('Unexpected files on gh-pages; refusing to overwrite.')
        existing={entry['path']:entry for entry in tree['tree']}
        def git_hash(content):
            encoded=content.encode('utf-8')
            return hashlib.sha1(b'blob '+str(len(encoded)).encode()+b'\0'+encoded).hexdigest()
        # Immutable files already known locally are verified by their Git blob
        # digest. Unknown older generations must prove their content address.
        for name,entry in existing.items():
            if not DATA_NAME.fullmatch(name):continue
            if name in files:
                if entry['sha'] != git_hash(files[name]):
                    raise RuntimeError('Unexpected public data content; refusing to overwrite.')
            else:
                blob=api('git/blobs/'+entry['sha'])
                content=base64.b64decode(blob['content']).decode()
                if hashlib.sha256(content.encode()).hexdigest() != name[12:-5]:
                    raise RuntimeError('Unexpected public data content; refusing to overwrite.')
        timed={'knowledge.json','site.json','site-manifest.json'}
        same=set(existing)==set(files) and all(existing[name]['sha']==git_hash(content) for name,content in files.items() if name not in timed)
        if same:
            old={}
            for name in timed:
                blob=api('git/blobs/'+existing[name]['sha'])
                old[name]=base64.b64decode(blob['content']).decode()
            if fingerprint(old)==fingerprint({name:files[name] for name in timed}):
                return dict(summary,status='unchanged',commit=parent)
    # Only independent blob creation is parallel. Tree/commit/ref publication is
    # sequential and happens after every asset has been uploaded successfully.
    from concurrent.futures import ThreadPoolExecutor
    def upload(item):
        name,content=item
        encoded=content.encode('utf-8')
        sha=hashlib.sha1(b'blob '+str(len(encoded)).encode()+b'\0'+encoded).hexdigest()
        if not parent or not any(e['path']==name and e['sha']==sha for e in tree['tree']):
            try:
                sha=api('git/blobs','POST',{'content':base64.b64encode(encoded).decode('ascii'),'encoding':'base64'})['sha']
            except RuntimeError as error:
                raise RuntimeError(f'{name}: {error}') from error
        return {'path':name,'mode':'100644','type':'blob','sha':sha}
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries=list(pool.map(upload,files.items()))
    tree=api('git/trees','POST',{'tree':entries})
    commit=api('git/commits','POST',{'message':'Sync reviewed read-only wiki site','tree':tree['sha'],'parents':[parent] if parent else []})
    if parent:
        api('git/refs/heads/'+BRANCH,'PATCH',{'sha':commit['sha'],'force':False})
    else:
        api('git/refs','POST',{'ref':'refs/heads/'+BRANCH,'sha':commit['sha']})
    return dict(summary,status='published',commit=commit['sha'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/news.sqlite3')
    parser.add_argument('--output',default='.runtime/wiki-site')
    parser.add_argument('--watch',action='store_true',help='Sync changed reviewed output every five minutes while running.')
    args=parser.parse_args()
    with open('.runtime/wiki-pages-sync.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:print(json.dumps(sync(args.db,args.output),ensure_ascii=False),flush=True)
            except Exception as error:
                print(json.dumps({'status':'error','type':type(error).__name__,'message':str(error)},ensure_ascii=False),flush=True)
                if not args.watch:raise SystemExit(1)
            if not args.watch:break
            time.sleep(300)
