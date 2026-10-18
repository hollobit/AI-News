"""Question-balanced evidence selection and reviewable prompt excerpts."""
from collections import defaultdict
from copy import deepcopy
import re
import sqlite3
import hashlib

from evidence_search import terms, query_anchors
from link_groups import canonical_url

MAX_PASSAGES = 20

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
    searches = [query+' '+expansions if expansions else query for query in queries]
    if hasattr(graph, 'workspace_store'):
        from context_index import materialize_many
        graph = materialize_many(graph, searches)
    groups = [build_retrieval(graph, query, ids) for query in searches]
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
    source_refs=set(selected)
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
        key = document(item); text = normalized(item.get('text', ''))
        siblings = by_document[key]
        if not text or any(text in normalized(e.get('text', '')) for e in siblings): continue
        contained = [e for e in siblings if normalized(e.get('text', '')) and normalized(e['text']) in text]
        if contained:
            for previous in contained:
                selected.pop(previous['id'], None); siblings.remove(previous)
                replacements[previous['id']] = item['id']
        elif len(selected) >= MAX_PASSAGES or len(siblings) >= 2:
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
    dependencies=getattr(graph,'workspace_dependencies',None)
    dependency_refs=getattr(graph,'workspace_dependency_refs',None)
    if dependencies and dependency_refs is not None:
        dependencies={key:value for key,value in dependencies.items() if source_refs & set(dependency_refs.get(key,()))}
    return dict(nodes=list(nodes.values()), edges=list(edges.values())[:40], evidence=evidence,
                no_hits=not evidence, limitations=[], coverage=coverage,
                workspace_dependencies=dependencies,
                workspace_as_of=getattr(graph,'workspace_as_of',None),
                retrieval=dict(method='axis-balanced-documents-v1', **coverage,
                               evidence_characters=sum(len(e.get('text', '')) for e in evidence)))


def normalized(text):
    """Only layout whitespace is equivalent; numbers, case and wording survive."""
    return re.sub(r'\s+', ' ', text).strip()


DETAILS = tuple(re.compile(pattern, re.I) for pattern in (
    r'\d+(?:[.,]\d+)?\s*(?:%|건|명|개|배|images?|users?|records?|samples?)\b',
    r'\b(?:20\d\d|January|February|March|April|May|June|July|August|September|October|November|December)\b|\d+월\s*\d+일',
    r'유출|탈취|피해|사고|실패|성공률|정확도|\b(?:leak\w*|expos\w*|stole|stolen|breach\w*|accuracy|failure\w*)\b',
    r'통해|때문|자격증명|프롬프트|\b(?:via|because|credential\w*|injection|mechanism)\b',
    r'다만|그러나|제외|한계|조건|미검증|\b(?:but|however|unless|only|except|limitation\w*)\b',
))


def focus(evidence, question, budget=1800):
    """Select diverse verbatim windows, retaining neighbors and explicit gaps.

    The budget is soft: sentences and adjacent qualifications are never cut.
    Offsets/hash refer to the unchanged source; the reviewer still sees it all.
    """
    text = evidence.get('text') or ''
    # Short excerpts are cheaper to read intact than to repair a false claim
    # about missing context. Only longer sources need window selection.
    if len(text) <= max(budget,min(3600,budget*2)): return dict(evidence)
    boundaries = sorted(set([0, len(text)] + [m.end() for m in re.finditer(r'(?<=[.!?。])\s+|\n+', text)]))
    spans = list(zip(boundaries, boundaries[1:]))
    query = set(terms(question))
    hits = [set(terms(text[start:end])) & query for start, end in spans]
    if not any(hits): return dict(evidence)
    detail = [{j for j, pattern in enumerate(DETAILS) if pattern.search(text[start:end])} for start,end in spans]
    candidates = []
    for i in range(len(spans)):
        if not hits[i] and not detail[i]: continue
        start = max(0, i-1); end = min(len(spans), i+2)
        # A qualification can continue beyond the first neighboring sentence.
        while end < len(spans) and DETAILS[-1].search(text[spans[end][0]:spans[end][1]]) and end < i+5:
            end += 1
        candidates.append((start,end))
    chosen=set(); covered=set(); seen_details=set()
    for _ in range(3):
        available=[]
        for start,end in candidates:
            new=set(range(start,end))-chosen
            if not new:continue
            cost=sum(spans[i][1]-spans[i][0] for i in chosen|new)
            if chosen and cost>budget:continue
            words=set().union(*(hits[i] for i in new))
            details=set().union(*(detail[i] for i in new))
            score=(3*len(words-covered)+5*len(details-seen_details),len(words),-cost,-start)
            available.append((score,new,words,details))
        if not available:break
        score,new,words,details=max(available,key=lambda x:x[0])
        if chosen and score[0]<=0:break
        chosen|=new;covered|=words;seen_details|=details
    windows=[]
    for i in sorted(chosen):
        start,end=spans[i]
        if windows and windows[-1]['end']==start:windows[-1]['end']=end
        else:windows.append(dict(start=start,end=end))
    result=dict(evidence,text='\n[중간 문맥 생략]\n'.join(text[w['start']:w['end']] for w in windows),
                excerpt_scope='verbatim_windows_with_gaps',excerpt_segments=windows,
                source_text_hash=hashlib.sha256(text.encode()).hexdigest())
    if len(windows)==1:
        result.update(excerpt_start=windows[0]['start'],excerpt_end=windows[0]['end'],excerpt_scope='contiguous_query_context')
    return result
