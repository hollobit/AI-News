"""Publish only the reviewed static snapshot to the dedicated gh-pages branch."""
import argparse
import base64
import fcntl
from concurrent.futures import ThreadPoolExecutor
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

class GitHubAPIError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def api(path, method='GET', body=None):
    command = ['gh', 'api', f'repos/{REPO}/{path}', '--method', method]
    if body is not None:command += ['--input', '-']
    try:
        result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                                text=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as error:
        raise GitHubAPIError(f'GitHub API {method} {path} timed out', retryable=True) from error
    if result.returncode:
        code = re.search(r'HTTP (\d{3})', result.stderr)
        status = int(code.group(1)) if code else None
        transient = status in {408, 500, 502, 503, 504} or bool(re.search(
            r'connection reset|TLS handshake timeout|i/o timeout|unexpected EOF', result.stderr, re.I))
        # Print only controlled status metadata, never subprocess/provider inputs.
        raise GitHubAPIError(f'GitHub API {method} {path} failed (HTTP {status or "unknown"})', retryable=transient)
    return json.loads(result.stdout) if result.stdout.strip() else {}

def immutable_object(path, body):
    """Blob/tree creation is content addressed; commit/ref updates are not."""
    if path not in {'git/blobs', 'git/trees'}:
        raise ValueError('Only immutable Git objects may be retried')
    for attempt in range(3):
        try:
            return api(path, 'POST', body)
        except GitHubAPIError as error:
            if not error.retryable or attempt == 2:
                raise
            time.sleep(2 ** attempt)


def fingerprint(files):
    normalized=dict(files)
    for name in ('knowledge.json','site.json','site-manifest.json'):
        if name not in normalized:continue
        data=json.loads(normalized[name]);data.pop('exported_at',None)
        normalized[name]=json.dumps(data,sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

# Large compatibility snapshots do not need base64 HTTP bodies. Git transports
# the same immutable objects and atomically updates only the dedicated ref.
GIT_THRESHOLD = 16 * 1024 * 1024


def git_publish(files, parent, *, root=None, expected_remote=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    def command(arguments, content=None):
        try:
            result = subprocess.run(['git', *arguments], cwd=root, input=content,
                capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('Pages Git transport timed out; check the remote ref before retrying') from error
        if result.returncode:
            raise RuntimeError('Pages Git transport failed at ' + arguments[0])
        return result.stdout.decode().strip()
    remote = command(['remote','get-url','origin'])
    allowed = {expected_remote} if expected_remote is not None else {
        'https://github.com/' + REPO + '.git', 'https://github.com/' + REPO,
        'git@github.com:' + REPO + '.git'}
    if remote not in allowed:
        raise RuntimeError('Unexpected Pages Git remote')
    if parent:
        exists = subprocess.run(['git','cat-file','-e',parent+'^{commit}'],cwd=root,capture_output=True)
        if exists.returncode:
            command(['fetch','--no-tags','--depth=1','origin',parent])
    entries = []
    for name, content in sorted(files.items()):
        # Callers already enforce published_files/dependency/content validation.
        # The tree primitive additionally refuses paths/modes outside flat assets.
        if '/' in name or '\t' in name or '\n' in name or name in ('.','..'):
            raise ValueError('Invalid Pages Git asset path')
        sha = command(['hash-object','-w','--stdin'], content.encode())
        entries.append('100644 blob ' + sha + '\t' + name + '\n')
    tree = command(['mktree'], ''.join(entries).encode())
    arguments = ['commit-tree',tree]
    if parent: arguments += ['-p',parent]
    commit = command(arguments, b'Sync reviewed read-only wiki site\n')
    # Never checkout/reset the user's source branch; no force or automatic ref retry.
    command(['push','origin',commit+':refs/heads/'+BRANCH])
    return commit


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
        def verify_existing(item):
            name,entry=item
            if not DATA_NAME.fullmatch(name):return
            if name in files:
                if entry['sha'] != git_hash(files[name]):
                    raise RuntimeError('Unexpected public data content; refusing to overwrite.')
            else:
                blob=api('git/blobs/'+entry['sha'])
                content=base64.b64decode(blob['content']).decode()
                if hashlib.sha256(content.encode()).hexdigest() != name[12:-5]:
                    raise RuntimeError('Unexpected public data content; refusing to overwrite.')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(verify_existing, existing.items()))
        timed={'knowledge.json','site.json','site-manifest.json'}
        same=set(existing)==set(files) and all(existing[name]['sha']==git_hash(content) for name,content in files.items() if name not in timed)
        if same:
            old={}
            for name in timed:
                blob=api('git/blobs/'+existing[name]['sha'])
                old[name]=base64.b64decode(blob['content']).decode()
            if fingerprint(old)==fingerprint({name:files[name] for name in timed}):
                return dict(summary,status='unchanged',commit=parent)
    if any(len(content.encode()) > GIT_THRESHOLD for content in files.values()):
        commit = git_publish(files, parent)
        return dict(summary, status='published', commit=commit, transport='git')
    # Only independent blob creation is parallel. Tree/commit/ref publication is
    # sequential and happens after every asset has been uploaded successfully.
    def upload(item):
        name,content=item
        encoded=content.encode('utf-8')
        sha=hashlib.sha1(b'blob '+str(len(encoded)).encode()+b'\0'+encoded).hexdigest()
        if not parent or not any(e['path']==name and e['sha']==sha for e in tree['tree']):
            try:
                sha=immutable_object('git/blobs',{'content':base64.b64encode(encoded).decode('ascii'),'encoding':'base64'})['sha']
            except RuntimeError as error:
                raise RuntimeError(f'{name}: {error}') from error
        return {'path':name,'mode':'100644','type':'blob','sha':sha}
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries=list(pool.map(upload,files.items()))
    tree=immutable_object('git/trees',{'tree':entries})
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
