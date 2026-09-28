"""Export a read-only Pages site. Never pushes code, DBs, credentials or raw text by default."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, urlunsplit
from knowledge_wiki import read_wiki
from wiki_network import project, source_id

ROOT = Path(__file__).resolve().parent


def public_url(value):
    try:
        u = urlsplit(value)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
            return ''
        if u.hostname in ('localhost','127.0.0.1','::1') or u.hostname.endswith('.local'):
            return ''
        return urlunsplit((u.scheme,u.netloc,u.path,'',''))
    except ValueError:
        return ''


def snapshot(db, include_excerpts=False):
    view = read_wiki(db, include_details=True)
    graph = project(view)
    nodes = []
    for original in graph['nodes']:
        node = {k:original[k] for k in ('id','type','title','page_ids','topics','source_ids','claims','claim_kind','origin','scope','day','status') if k in original}
        if original.get('url'):
            node['url']=public_url(original['url'])
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
    target = Path(output).resolve()
    marker = target/'knowledge.json'
    allowed={'knowledge.json','index.html','public-data.js','workspace.js','workspace.css','wiki-network.js','wiki-network-3d.js','wiki-network.css','three.module.js','three.core.js','three.LICENSE','.nojekyll','README.md'}
    if full_site:
        from public_site import published_files
        allowed=set(published_files(target))
    if target.exists() and (any(p.is_symlink() or p.name not in allowed for p in target.iterdir()) or (any(target.iterdir()) and not marker.is_file())):
        raise ValueError('비어 있거나 이 도구가 만든 전용 출력 폴더를 사용하세요.')
    with sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        data=snapshot(db,include_excerpts)
        if full_site:
            from public_site import content, observation
            from corpus_knowledge import expand
            corpus=content(db)
            observed_path=Path(str(db_path)+'.observatory')/'90-expanded-v2.json'
            observed=observation(json.loads(observed_path.read_text())) if observed_path.exists() else {}
            expand(data,corpus,observed)
    target.mkdir(parents=True,exist_ok=True)
    from site_templates import public_html
    html=public_html((ROOT/'static/wiki-network.html').read_text().replace('data-mode="live"','data-mode="static"'))
    (target/'index.html').write_text(html)
    if full_site:html=html.replace('data-mode="static"','data-mode="static" data-split="true"')
    (target/'index.html').write_text(html)
    for name in ('public-data.js','workspace.js','workspace.css','wiki-network.js','wiki-network-3d.js','wiki-network.css','three.module.js','three.core.js','three.LICENSE'):(target/name).write_bytes((ROOT/'static'/name).read_bytes())
    marker.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    (target/'.nojekyll').write_text('')
    (target/'README.md').write_text('# 읽기 전용 지식 위키\n\n이 폴더만 Pages 전용 저장소에 게시합니다. 데이터는 게시 시점의 스냅샷입니다.\nDB·환경 설정·원격 인증 파일을 추가하지 마세요.\n')
    if full_site:
        from public_site import write_site
        (target/'knowledge.html').write_text(html.replace('<body>','<body><nav style="padding:12px"><a href="index.html">← 전체 메뉴 · 뉴스 분석</a> · <a href="observatory.html">관측 지도</a> · <a href="index.html?view=papers">논문</a></nav>').replace('</body>','<script src="public-navigation.js"></script></body>'))
        write_site(db_path,target,ROOT,data['exported_at'],data=corpus)
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
