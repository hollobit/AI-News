"""Question-balanced evidence selection and reviewable prompt excerpts."""
from collections import defaultdict
from copy import deepcopy
import re
import sqlite3

from evidence_search import terms, query_anchors
from link_groups import canonical_url

# Search vocabulary, not inferred facts. Apply only facets the user requested.
FACETS = (
    (r'벤치마크|\bbenchmarks?\b', 'benchmark benchmarks 벤치마크'),
    (r'데이터셋|데이터세트|\bdatasets?\b', 'dataset datasets 데이터셋 데이터세트'),
    (r'표준(?:화)?|\bstandard(?:s|ization|isation)?\b', 'standard standards standardization 표준 표준화'),
    (r'안전|\bsafety\b', 'safety 안전'),
    (r'비용|\bcosts?\b', 'cost costs 비용'),
)


def requested_facets(question):
    return [(re.compile(pattern,re.I),words) for pattern,words in FACETS if re.search(pattern,question,re.I)]


def focused_candidates(items, question, facets):
    """Prefer the requested subject/facet over an incidental topic mention.

    Keep two broader contextual sources per axis; do not silently replace an
    unsupported axis with a full budget of generic articles.
    """
    if not facets:return items
    anchors=query_anchors(question)
    primary=[];context=[]
    for item in items:
        title=item.get('title') or '';text=title+' '+(item.get('text') or '')
        if not any(pattern.search(text) for pattern,_ in facets):continue
        if not anchors or set(terms(title)) & anchors:primary.append(item)
        else:context.append(item)
    return primary+context[:2]


def document(evidence):
    return canonical_url(evidence.get('source_url') or evidence.get('url') or '') or evidence.get('document_id') or evidence['id']


def compose(graph, question, ids, plan, path=None, scoped=False):
    from graph_rag import build_retrieval
    queries = list(dict.fromkeys([question] + plan['queries']))
    facets=requested_facets(question)
    expansions=' '.join(words for _,words in facets)
    groups = [build_retrieval(graph, query+' '+expansions if expansions else query, ids) for query in queries]
    groups = [dict(g,evidence=focused_candidates(g['evidence'],question,facets)) for g in groups]
    selected = {}; documents = set()
    # Reserve space for each search axis BEFORE adding longer source passages.
    for position in range(max((len(g['evidence']) for g in groups), default=0)):
        for group in groups:
            if position >= len(group['evidence']) or len(selected) >= 16: continue
            item = group['evidence'][position]
            key = document(item)
            if key not in documents:
                selected[item['id']] = item; documents.add(key)
    supplements = []
    if path:
        from source_store import search
        from knowledge_wiki import retrieve
        with sqlite3.connect(path, timeout=.5) as db:
            db.row_factory = sqlite3.Row
            allowed = {e.get('source_url', '') for e in selected.values()} if ids or scoped else None
            supplements = search(db, question, limit=24, urls=allowed)
            if not ids and not scoped: supplements += retrieve(db, question, limit=1)
    by_document = defaultdict(list)
    for item in selected.values(): by_document[document(item)].append(item)
    query_terms = set(terms(' '.join(queries)))
    anchors=query_anchors(question)
    def relevance(item):
        text=item.get('text', '')
        return (bool(set(terms(item.get('title',''))) & anchors),
                sum(bool(pattern.search(text)) for pattern,_ in facets),
                len(set(terms(text)) & query_terms), min(len(text), 1600))
    replacements = {}
    # An identical/contained passage is one source, not corroboration. A longer
    # passage can replace an exact contained excerpt without losing its context.
    for item in sorted(supplements, key=relevance, reverse=True):
        key = document(item); text = item.get('text', '').strip()
        siblings = by_document[key]
        if not text or any(text in e.get('text', '') for e in siblings): continue
        contained = [e for e in siblings if e.get('text', '').strip() and e['text'].strip() in text]
        if contained:
            for previous in contained:
                selected.pop(previous['id'], None); siblings.remove(previous)
                replacements[previous['id']] = item['id']
        elif len(selected) >= 20 or len(siblings) >= 2:
            continue
        selected[item['id']] = item; siblings.append(item)
    refs = set(selected)
    def attached(item):
        value = deepcopy(item)
        def current(ref):
            while ref in replacements: ref = replacements[ref]
            return ref
        value['evidence_ids'] = list(dict.fromkeys(current(r) for r in value.get('evidence_ids', []) if current(r) in refs))
        return value
    nodes = {}
    # Merge references from every query; later axes must not erase earlier ones.
    for group in groups:
        for original in group['nodes']:
            candidate = attached(original)
            if candidate['evidence_ids']:
                node = nodes.setdefault(original['id'], candidate)
                node['evidence_ids'] = sorted(set(node['evidence_ids']) | set(candidate['evidence_ids']))
    nodes = dict(list(nodes.items())[:24])
    edges = {}
    for group in groups:
        for original in group['edges']:
            edge = attached(original)
            if edge['evidence_ids'] and edge['source'] in nodes and edge['target'] in nodes:
                if edge['id'] in edges:
                    edge['evidence_ids'] = sorted(set(edge['evidence_ids']) | set(edges[edge['id']]['evidence_ids']))
                edges[edge['id']] = edge
    evidence = list(selected.values())
    coverage = dict(scope='question_selected_evidence', unique_documents=len({document(e) for e in evidence}),
                    passages=len(evidence), full_corpus_analysis=False)
    return dict(nodes=list(nodes.values()), edges=list(edges.values())[:40], evidence=evidence,
                no_hits=not evidence, limitations=[], coverage=coverage,
                retrieval=dict(method='axis-balanced-documents-v1', **coverage,
                               evidence_characters=sum(len(e.get('text', '')) for e in evidence)))


def focus(evidence, question, budget=1600):
    """One contiguous sentence window; the independent reviewer gets the full text.

    Never join distant sentences or remove a qualifier from inside a sentence.
    Budget is soft: an indivisible long sentence remains intact.
    """
    text = evidence.get('text') or ''
    if len(text) <= budget: return dict(evidence)
    boundaries = sorted(set([0, len(text)] + [m.end() for m in re.finditer(r'(?<=[.!?。])\s+|\n+', text)]))
    spans = list(zip(boundaries, boundaries[1:]))
    query = set(terms(question))
    hits = [set(terms(text[start:end])) & query for start, end in spans]
    best = None
    for i in range(len(spans)):
        if not hits[i]: continue
        # Include the preceding and following sentences for qualifications.
        start = max(0, i-1); end = min(len(spans), i+2)
        while end < len(spans) and spans[end][1]-spans[start][0] <= budget: end += 1
        if end < len(spans) and hits[end-1]: end += 1
        covered = set().union(*hits[start:end])
        score = (len(covered), sum(len(h) for h in hits[start:end]), -start)
        if best is None or score > best[0]: best = (score, spans[start][0], spans[end-1][1])
    if best is None: return dict(evidence)
    _, start, end = best
    return dict(evidence, text=text[start:end], excerpt_scope='contiguous_query_context',
                excerpt_start=start, excerpt_end=end)
