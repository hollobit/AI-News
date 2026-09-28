"""Versioned, allowlisted shards over an already sanitized public snapshot."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

MANIFEST = 'site-manifest.json'
DATA_NAME = re.compile(r'public-data-[0-9a-f]{64}\.json\Z')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def bucket(identity):
    # Partition routing is not an integrity/security hash. FNV permits the same
    # public files to work on ordinary HTTP preview origins without WebCrypto.
    value=2166136261
    for byte in identity.encode():value=((value^byte)*16777619)&0xffffffff
    return f'{value&255:02x}'


def data_files(root):
    path = Path(root) / MANIFEST
    if not path.exists():
        return ()
    manifest = json.loads(path.read_text())
    names = manifest.get('files', []) + manifest.get('previous_files', [])
    if not isinstance(names, list) or any(not isinstance(n, str) or not DATA_NAME.fullmatch(n) for n in names):
        raise ValueError('Invalid public data manifest')
    return tuple(sorted(set(names)))


def write_data(root, corpus, graph, observatory=None):
    news_ids = [row['id'] for row in corpus['news']]
    node_ids = [row['id'] for row in graph['nodes']]
    edge_ids = [row['id'] for row in graph['edges']]
    if any(len(ids) != len(set(ids)) for ids in (news_ids, node_ids, edge_ids)):
        raise ValueError('Duplicate public identities')
    nodes = set(node_ids)
    if any(edge['source'] not in nodes or edge['target'] not in nodes for edge in graph['edges']):
        raise ValueError('Missing public graph endpoint')
    root = Path(root)
    previous = json.loads((root / MANIFEST).read_text()).get('files', []) if (root / MANIFEST).exists() else []
    files = set()

    def put(value):
        body = encoded(value)
        name = 'public-data-' + hashlib.sha256(body).hexdigest() + '.json'
        (root / name).write_bytes(body)
        files.add(name)
        return name

    groups = defaultdict(list)
    for position, row in enumerate(corpus['news']):
        groups[str(position // 100)].append(row)
    news_parts = {key: put(rows) for key, rows in groups.items()}
    news_index = put([[n['id'], n['title'], n['day'], n['topic'], n['url'], len(n['analyses']), str(i // 100)] for i, n in enumerate(corpus['news'])])
    # Keep all fields searched by the old UI; load this complete index only when
    # a query is entered. No partial-shard search is presented as global search.
    search = {}
    for n in corpus['news']:
        values = [n.get(k) for k in ('title', 'topic', 'url', 'summary', 'current_basis', 'scenario', 'uncertainty')]
        values += n.get('assumptions', []) + n.get('mitigations', [])
        values += [a.get(k) for a in n.get('analyses', []) for k in ('title', 'text', 'uncertainty')]
        values += [a.get(k) for a in n.get('claims', []) for k in ('title', 'text', 'detail', 'uncertainty')]
        search[n['id']] = ' '.join(str(v) for v in values if v)
    node_groups, edge_groups = defaultdict(list), defaultdict(list)
    nodes = {n['id']: n for n in graph['nodes']}
    adjacency = defaultdict(list)
    for e in graph['edges']:
        adjacency[e['source']].append(e)
        adjacency[e['target']].append(e)
        for key in {bucket(e['source']), bucket(e['target'])}:
            edge_groups[key].append(e)
    for position, n in enumerate(graph['nodes']):
        node_groups[bucket(n['id'])].append(dict(n, _order=position))
    graph_parts = {key: put({'nodes': rows, 'edges': edge_groups[key]}) for key, rows in node_groups.items()}
    meta = {k: v for k, v in graph.items() if k not in ('nodes', 'edges', 'pages', 'exported_at')}
    ordered = graph['nodes'][:500]
    ids = {n['id'] for n in ordered}
    bootstrap = put(dict(meta, nodes=ordered, edges=[e for e in graph['edges'] if e['source'] in ids and e['target'] in ids]))
    # Full title and ordering are preserved for arbitrary search and layer filters.
    graph_index = put([[n['id'], n['title'], sorted({e['layer'] for e in adjacency[n['id']]}), n.get('url', ''), n.get('news_id', '')] for n in graph['nodes']])
    wiki_sources = {sid for p in graph['pages'] for c in p['claims'] for sid in c['evidence_ids']}
    wiki_nodes = [n for n in graph['nodes'] if n['id'] in wiki_sources or n.get('page_ids')]
    manifest = dict(schema_version=1, exported_at=corpus['exported_at'], coverage=corpus['coverage'],
        news=dict(index=news_index, search=put(search), parts=news_parts),
        papers=put(corpus['papers']), risks=put(corpus['risks']), risk_graph=put(corpus.get('risk_graph', {})),
        wiki=put({'pages': graph['pages'], 'nodes': wiki_nodes}),
        graph=dict(meta=meta, total=len(nodes), bootstrap=bootstrap, index=graph_index, parts=graph_parts,
            lookup=put({'news': {n['news_id']: n['id'] for n in graph['nodes'] if n.get('news_id')},
                        'urls': {n['url']: n['id'] for n in graph['nodes'] if n.get('url') and n.get('type') == 'source'}})))
    observed = dict(observatory or {'nodes': [], 'edges': [], 'days': []})
    observed.pop('documents', None)
    observed['nodes'] = [{k: v for k, v in n.items() if k != 'document_ids_by_day'} for n in observed.get('nodes', [])]
    manifest['observatory'] = put(observed)
    manifest['version'] = hashlib.sha256(encoded({k: v for k, v in manifest.items() if k != 'exported_at'})).hexdigest()
    manifest.update(files=sorted(files), previous_files=sorted(set(previous) - files))
    temporary = root / (MANIFEST + '.tmp')
    temporary.write_bytes(encoded(manifest))
    temporary.replace(root / MANIFEST)
    # Keep the preceding generation for open tabs; only remove our own older
    # content-addressed files, never arbitrary output paths.
    retained = set(data_files(root))
    for path in root.glob('public-data-*.json'):
        if DATA_NAME.fullmatch(path.name) and path.name not in retained and not path.is_symlink():
            path.unlink()
    return manifest
