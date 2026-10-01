"""Export a read-only Pages site. Never pushes code, DBs, credentials or raw text by default."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, urlunsplit, unquote_plus
from knowledge_wiki import read_wiki
from wiki_network import project, source_id

ROOT = Path(__file__).resolve().parent


def _public_parameters(value):
    sensitive = {'secret','token','access_token','refresh_token','api_key','apikey','key',
                 'password','passwd','authorization','auth','signature','sig','hmac',
                 'credential','policy','key-pair-id','session','sessionid','jwt'}
    keys = {unquote_plus(p.partition('=')[0]).lower() for p in value.split('&')}
    if keys & {'state', 'client_id', 'redirect_uri'}:
        sensitive.add('code')
    kept = []
    for part in value.split('&'):
        key, _, payload = part.partition('=')
        name = unquote_plus(key).lower().replace('-', '_')
        if name in {k.replace('-', '_') for k in sensitive} or name.startswith(('x_amz_', 'x_goog_')):
            continue
        decoded = unquote_plus(payload)
        if decoded.startswith(('http://', 'https://')) and public_url(decoded) != decoded:
            continue
        kept.append(part)
    return '&'.join(kept)


def public_url(value, db=None):
    try:
        if db is not None:
            from source_navigation import original_url
            value = original_url(db, value)
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            return ''
        u = urlsplit(value)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            return ''
        if u.hostname in ('localhost','127.0.0.1','::1') or u.hostname.endswith('.local'):
            return ''
        query = _public_parameters(u.query)
        fragment = _public_parameters(u.fragment) if query == u.query else ''
        return urlunsplit((u.scheme,u.netloc,u.path,query,fragment))
    except ValueError:
        return ''


def snapshot(db, include_excerpts=False):
    view = read_wiki(db, include_details=True)
    graph = project(view)
    nodes = []
    for original in graph['nodes']:
        node = {k:original[k] for k in ('id','type','title','page_ids','topics','source_ids','claims','claim_kind','origin','scope','day','status') if k in original}
        if original.get('url'):
            node['url']=public_url(original['url'], db)
            from public_site import identity
            from link_groups import canonical_url
            if not node['id'].startswith('source:paper:'):
                node['news_id']=identity(canonical_url(original['url']))
        if include_excerpts and node['type']=='source':node['excerpt']=graph['sources'][node['id']]['text']
        nodes.append(node)
    pages=[]
    for page in view['pages']:
        refs={e['id']:source_id(e) for e in page['evidence']}
        public={k:page[k] for k in ('id','title','kind','revision','scope')}
        public['claims']=[dict(c,evidence_ids=[refs[r] for r in c['evidence_ids']]) for c in page['claims']]
        pages.append(public)
    return dict(nodes=nodes,edges=graph['edges'],pages=pages,issues=[],version=graph['version'],
        exported_at=datetime.now(timezone.utc).isoformat(),
        coverage=dict(pages=len(pages),claims=sum(n['type']=='claim' for n in nodes),cited_sources=len(graph['sources']),all_sources_compiled=False),
        method='생성 시점에 입력과 독립 검토가 일치한 요약·관계·출처 링크입니다. 전체 자료의 분석 완료나 사실성 보증이 아닙니다.',
        privacy=dict(raw_excerpts=include_excerpts,internal_jobs=False,dependencies=False,credentials=False))


def export_site(db_path, output, include_excerpts=False, full_site=False):
    from export_staging import generation, output_lock, logical_path
    from public_site import PUBLIC_FILES, published_files
    from public_data import data_files
    from static_dependencies import validate_static_dependencies
    legacy = {'knowledge.json','index.html','workspace-navigation.js','workspace-ui.css','public-data.js','workspace.js','workspace.css',
        'wiki-network.js','wiki-network-3d.js','wiki-network.css','three.module.js','three.core.js',
        'three.LICENSE','.nojekyll','README.md'}
    allowed = published_files if full_site else lambda root: legacy
    with output_lock(output):
        with generation(output, allowed) as target:
            result = _export_site(db_path, target, include_excerpts, full_site)
            names = published_files(target) if full_site else legacy
            validate_static_dependencies(target, names)
        result['output'] = str(logical_path(output))
        result['snapshot_directory'] = str(logical_path(output).resolve())
        return result


def _export_site(db_path, target, include_excerpts=False, full_site=False):
    import hashlib
    from public_site import ASSETS
    # Read each asset and observation once. Every generated page in this build
    # uses these exact bytes even when the source checkout/views change later.
    asset_names = set(ASSETS) | {'wiki-network.html','wiki-network.js','wiki-network-3d.js',
        'wiki-network.css','three.module.js','three.core.js','three.LICENSE'}
    assets = {name: (ROOT/'static'/name).read_bytes() for name in asset_names}
    observations = {}
    for days in (14,30,90):
        for mode in ('default','expanded'):
            name=f'observatory-{days}-{mode}.json'
            path=Path(str(db_path)+'.observatory')/f'{days}-{mode}-v2.json'
            observations[name]=json.loads(path.read_text()) if path.exists() else {}
    marker = target/'knowledge.json'
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        revisions = dict(db.execute('SELECT kind,value FROM projection_revisions')) if db.execute("SELECT 1 FROM sqlite_master WHERE name='projection_revisions'").fetchone() else {}
        data=snapshot(db,include_excerpts)
        if full_site:
            from public_site import content, observation
            from corpus_knowledge import expand
            corpus=content(db)
            from source_navigation import original_url
            from functools import lru_cache
            resolve = lru_cache(maxsize=None)(lambda value: original_url(db, value))
            for raw in observations.values():
                for field in ('evidence', 'documents'):
                    for entry in raw.get('data', {}).get(field, {}).values():
                        if entry.get('url'):
                            entry['url'] = resolve(entry['url'])
            observed=observation(observations['observatory-90-expanded.json'])
            expand(data,corpus,observed)
    target.mkdir(parents=True,exist_ok=True)
    from site_templates import public_html
    html=public_html(assets['wiki-network.html'].decode().replace('data-mode="live"','data-mode="static" data-public="true"'))
    (target/'index.html').write_text(html)
    if full_site:html=html.replace('data-mode="static"','data-mode="static" data-split="true"')
    (target/'index.html').write_text(html)
    for name in ('workspace-navigation.js','workspace-ui.css','public-data.js','workspace.js','workspace.css','wiki-network.js','wiki-network-3d.js','wiki-network.css','three.module.js','three.core.js','three.LICENSE'):(target/name).write_bytes(assets[name])
    # The compatibility graph retains every field but avoids tens of MiB of
    # pretty-print padding in full-site blob uploads.
    format_options = {'separators': (',', ':')} if full_site else {'indent': 2}
    marker.write_text(json.dumps(data,ensure_ascii=False,**format_options))
    (target/'.nojekyll').write_text('')
    (target/'README.md').write_text('# 읽기 전용 지식 위키\n\n이 폴더만 Pages 전용 저장소에 게시합니다. 데이터는 게시 시점의 스냅샷입니다.\nDB·환경 설정·원격 인증 파일을 추가하지 마세요.\n')
    if full_site:
        from public_site import write_site
        (target/'knowledge.html').write_text(html.replace('</body>','<script src="public-navigation.js"></script></body>'))
        write_site(db_path,target,ROOT,data['exported_at'],data=corpus,assets=assets,observations=observations)
        descriptor = {'schema_version':1,'db_revisions':revisions,
            'assets':{name:hashlib.sha256(body).hexdigest() for name,body in sorted(assets.items())},
            'observations':{name:{key:raw.get('data',{}).get(key) for key in ('version','computed_at','days','comparison','selection_version')} for name,raw in observations.items()}}
        (target/'build.json').write_text(json.dumps(descriptor,ensure_ascii=False,separators=(',',':')))
        from public_data import write_data
        write_data(target,corpus,data,json.loads((target/'observatory-14-default.json').read_text()))
    return dict(output=str(target),pages=len(data['pages']),nodes=len(data['nodes']),sources=data['coverage']['cited_sources'],raw_excerpts=include_excerpts)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/news.sqlite3')
    parser.add_argument('--output',default='.runtime/wiki-site')
    parser.add_argument('--include-excerpts',action='store_true',help='Explicitly include original excerpts; review publication rights first.')
    parser.add_argument('--full-site',action='store_true')
    args=parser.parse_args()
    print(json.dumps(export_site(args.db,args.output,args.include_excerpts,args.full_site),ensure_ascii=False))
