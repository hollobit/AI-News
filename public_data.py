"""Versioned, allowlisted shards over an already sanitized public snapshot."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import time

MANIFEST = 'site-manifest.json'
RETENTION_FILE = '.public-data-retention.json'

def retention_path(root):
    root = Path(root).resolve()
    return root.with_name(root.name + RETENTION_FILE)
RETENTION_SECONDS = 24 * 60 * 60
RETENTION_BYTES = 400 * 1024 * 1024

DATA_NAME = re.compile(r'public-data-[0-9a-f]{64}\.json\Z')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def bucket(identity, bits=8):
    # Partition routing is not an integrity/security hash. FNV permits the same
    # public files to work on ordinary HTTP preview origins without WebCrypto.
    value=2166136261
    for byte in identity.encode():value=((value^byte)*16777619)&0xffffffff
    return format(value & ((1 << bits) - 1), f'0{(bits + 3) // 4}x')


def data_files(root):
    path = Path(root) / MANIFEST
    if not path.exists():
        return ()
    manifest = json.loads(path.read_text())
    names = manifest.get('files', []) + manifest.get('previous_files', [])
    ledger = retention_path(root)
    generations = json.loads(ledger.read_text()) if ledger.exists() else manifest.get('retained_generations', [])
    for generation in generations:
        names += generation['files']
    if not isinstance(names, list) or any(not isinstance(n, str) or not DATA_NAME.fullmatch(n) for n in names):
        raise ValueError('Invalid public data manifest')
    return tuple(sorted(set(names)))


def bounded_generations(root, current, generations):
    selected = set(current)
    used = sum((root / name).stat().st_size for name in selected)
    kept = []
    for generation in sorted(generations, key=lambda g: g['expires_at'], reverse=True):
        extra = set(generation['files']) - selected
        size = sum((root / name).stat().st_size for name in extra)
        if used + size > RETENTION_BYTES:
            break
        selected.update(extra)
        used += size
        kept.append(generation)
    return list(reversed(kept))


def write_data(root, corpus, graph, observatory=None, *, now=None):
    news_ids = [row['id'] for row in corpus['news']]
    node_ids = [row['id'] for row in graph['nodes']]
    edge_ids = [row['id'] for row in graph['edges']]
    if any(len(ids) != len(set(ids)) for ids in (news_ids, node_ids, edge_ids)):
        raise ValueError('Duplicate public identities')
    nodes = set(node_ids)
    if any(edge['source'] not in nodes or edge['target'] not in nodes for edge in graph['edges']):
        raise ValueError('Missing public graph endpoint')
    root = Path(root)
    stamp = time.time() if now is None else now
    old = json.loads((root / MANIFEST).read_text()) if (root / MANIFEST).exists() else {}
    previous = old.get('files', [])
    ledger = retention_path(root)
    generations = json.loads(ledger.read_text()) if ledger.exists() else old.get('retained_generations', [])
    retained = [g for g in generations if g['expires_at'] > stamp]
    files = set()

    def put(value):
        body = encoded(value)
        name = 'public-data-' + hashlib.sha256(body).hexdigest() + '.json'
        if not (root / name).exists():
            (root / name).write_bytes(body)
        elif (root / name).read_bytes() != body:
            raise ValueError('Invalid retained content-addressed file')
        files.add(name)
        return name

    groups = defaultdict(list)
    for row in corpus['news']:
        groups[bucket(row['id'], 10)].append(row)
    news_parts = {key: put(sorted(rows, key=lambda n: n['id'])) for key, rows in groups.items()}
    news_index = put([[n['id'], n['title'], n['day'], n['topic'], n['url'], len(n['analyses']), bucket(n['id'], 10)] for n in corpus['news']])
    # Keep all fields searched by the old UI; load this complete index only when
    # a query is entered. No partial-shard search is presented as global search.
    search = {}
    for n in corpus['news']:
        values = [n.get(k) for k in ('title', 'source_text', 'briefing_title', 'source_title', 'topic', 'url', 'summary', 'current_basis', 'scenario', 'uncertainty')]
        values += n.get('assumptions', []) + n.get('mitigations', [])
        values += [a.get(k) for a in n.get('analyses', []) for k in ('title', 'text', 'uncertainty')]
        values += [a.get(k) for a in n.get('claims', []) for k in ('title', 'text', 'detail', 'uncertainty')]
        search[n['id']] = '\n'.join(str(v) for v in values if v)
    node_groups, edge_groups = defaultdict(list), defaultdict(list)
    nodes = {n['id']: n for n in graph['nodes']}
    adjacency = defaultdict(list)
    for e in graph['edges']:
        adjacency[e['source']].append(e)
        adjacency[e['target']].append(e)
        for key in {bucket(e['source']), bucket(e['target'])}:
            edge_groups[key].append(e)
    for n in graph['nodes']:
        node_groups[bucket(n['id'])].append(n)
    graph_parts = {}
    for key, rows in node_groups.items():
        # Compact endpoint labels/order let the client choose a page before
        # hydrating neighbors. Full analyses remain in each node's own shard.
        endpoints = {e[k] for e in edge_groups[key] for k in ('source', 'target')}
        neighbors = {sid: dict(id=sid, title=nodes[sid]['title'], type=nodes[sid].get('type'))
                     for sid in sorted(endpoints)}
        graph_parts[key] = put({'nodes': sorted(rows, key=lambda n: n['id']),
            'edges': sorted(edge_groups[key], key=lambda e: e['id']), 'neighbors': neighbors})
    meta = {k: v for k, v in graph.items() if k not in ('nodes', 'edges', 'pages', 'exported_at')}
    ordered = graph['nodes'][:500]
    ids = {n['id'] for n in ordered}
    bootstrap = put(dict(meta, nodes=ordered, edges=[e for e in graph['edges'] if e['source'] in ids and e['target'] in ids]))
    # Full title and ordering are preserved for arbitrary search and layer filters.
    graph_index = put([[n['id'], n['title'], sorted({e['layer'] for e in adjacency[n['id']]}), n.get('url', ''), n.get('news_id', '')] for n in graph['nodes']])
    wiki_sources = {sid for p in graph['pages'] for c in p['claims'] for sid in c['evidence_ids']}
    wiki_nodes = [n for n in graph['nodes'] if n['id'] in wiki_sources or n.get('page_ids')]
    manifest = dict(schema_version=1, exported_at=corpus['exported_at'], coverage=corpus['coverage'],
        news=dict(index=news_index, search=put(search), parts=news_parts,
            bootstrap=put(corpus['news'][:40]), bootstrap_ids=news_ids[:40]),
        papers=put(corpus['papers']), risks=put(corpus['risks']), risk_graph=put(corpus.get('risk_graph', {})),
        wiki=put({'pages': graph['pages'], 'nodes': wiki_nodes}),
        graph=dict(meta=meta, total=len(nodes), bootstrap=bootstrap, index=graph_index, parts=graph_parts,
            order=put(node_ids),
            lookup=put({'news': {n['news_id']: n['id'] for n in graph['nodes'] if n.get('news_id')},
                        'urls': {n['url']: n['id'] for n in graph['nodes'] if n.get('url') and n.get('type') == 'source'}})))
    observed = dict(observatory or {'nodes': [], 'edges': [], 'days': []})
    observed.pop('documents', None)
    observed['nodes'] = [{k: v for k, v in n.items() if k != 'document_ids_by_day'} for n in observed.get('nodes', [])]
    manifest['observatory'] = put(observed)
    manifest['version'] = hashlib.sha256(encoded({k: v for k, v in manifest.items() if k != 'exported_at'})).hexdigest()
    if old.get('version') and old['version'] != manifest['version']:
        retained.append(dict(version=old['version'], expires_at=stamp + RETENTION_SECONDS, files=previous))
    # Keep complete newest generations within a byte budget. Current data is
    # never evicted; old tabs already recover through the fresh manifest on 404.
    retained = bounded_generations(root, files, retained)
    # Retention bookkeeping does not change the immutable content generation.
    manifest.update(files=sorted(files), previous_files=[],
                    retention_seconds=RETENTION_SECONDS)
    # The retention ledger is local exporter bookkeeping, not a browser payload.
    # published_files resolves its hashes but never publishes this ledger.
    ledger_tmp = ledger.with_suffix('.tmp')
    ledger_tmp.write_bytes(encoded(retained))
    ledger_tmp.replace(ledger)
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
