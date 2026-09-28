"""Evidence-first retrieval with query relevance, graph context and source diversity."""
from collections import defaultdict, OrderedDict
from copy import deepcopy
import threading
import time
from urllib.parse import urlsplit

from evidence_search import LexicalIndex, terms, query_anchors, VERSION

_LOCK = threading.Lock()
_INDEXES = OrderedDict()


class RetrievalIndex:
    def __init__(self, graph):
        self.nodes = {n['id']:n for n in getattr(graph,'full_nodes',graph.get('nodes',[]))}
        self.edges = list(getattr(graph,'full_edges',graph.get('edges',[])))
        self.evidence = {e['id']:e for e in getattr(graph,'full_evidence',graph.get('evidence',[]))}
        self.refs = defaultdict(set)
        self.adjacency = defaultdict(list)
        rows = {}
        for identity,e in self.evidence.items():
            rows[('e',identity)] = [(e.get('title',''),3),(e.get('text',''),1)]
        for identity,n in self.nodes.items():
            rows[('n',identity)] = [(n.get('name','')+' '+' '.join(n.get('aliases',[])),3),
                                  (n.get('summary','')[:1600],1)]
            for ref in n.get('evidence_ids',[]):
                if ref in self.evidence:self.refs[ref].add(identity)
        for e in self.edges:
            self.adjacency[e['source']].append(e)
            self.adjacency[e['target']].append(e)
        self.lexical = LexicalIndex(rows)


def get_index(graph):
    prepared = getattr(graph,'prepared_index',None)
    if prepared is not None:
        return prepared, True
    key = getattr(graph,'search_key',None)
    if key is None:
        return RetrievalIndex(graph), False
    with _LOCK:
        key = (VERSION,key)
        if key in _INDEXES:
            _INDEXES.move_to_end(key)
            return _INDEXES[key], True
        value = RetrievalIndex(graph)
        _INDEXES[key] = value
        # Bound retained corpora; a source/validation revision always gets a new index.
        while len(_INDEXES)>2:_INDEXES.popitem(last=False)
        return value, False


def retrieve(graph, question, node_ids=None):
    from graph_rag import RETRIEVAL_NODE_LIMIT, RETRIEVAL_EDGE_LIMIT, RETRIEVAL_EVIDENCE_LIMIT
    started = time.monotonic()
    index,hit = get_index(graph)
    scores = index.lexical.search(question)
    ns = {i:s for (kind,i),s in scores.items() if kind=='n'}
    es = {i:s for (kind,i),s in scores.items() if kind=='e'}
    requested = {i for i in node_ids or [] if i in index.nodes}
    scope = None
    if requested:
        scope = set(requested)
        frontier = set(requested)
        for _ in range(2):
            frontier = {edge[k] for i in frontier for edge in index.adjacency[i]
                        for k in ('source','target')} - scope
            scope |= frontier
        allowed = {r for i in scope for r in index.nodes[i].get('evidence_ids',[])}
        es = {i:s for i,s in es.items() if i in allowed}
        ns = {i:s for i,s in ns.items() if i in scope}
        for i in requested:
            for ref in index.nodes[i].get('evidence_ids',[]):
                if ref in index.evidence:es[ref] = es.get(ref,0)+2
    # Node matches bring their actual supporting documents into retrieval, while
    # high-degree generic hubs cannot win solely through support_count.
    for identity,score in sorted(ns.items(),key=lambda p:(-p[1],p[0]))[:24]:
        refs = index.nodes[identity].get('evidence_ids',[])
        contribution = score/(1+len(refs)**.5)
        for ref in refs:
            if ref in index.evidence:es[ref] = es.get(ref,0)+contribution
    candidates = sorted(es,key=lambda i:(-es[i],i))[:160]
    chosen = []
    documents = set()
    publishers = defaultdict(int)
    query_terms = set(terms(question))
    covered = set()
    # Query coverage is already available in postings. Do not tokenize every
    # candidate excerpt and matched node again on each question.
    token_sets = {i:{term for term in query_terms if ('e',i) in index.lexical.postings.get(term,{})}
                  for i in candidates}
    anchors = query_anchors(question)
    if anchors:
        # A rare incidental word such as "cost" must not replace the subject of
        # a question about sovereign AI. Return fewer sources when support is thin.
        interpreted = {ref for identity in ns
                       if len(index.nodes[identity].get('evidence_ids',[]))<=8
                       and any(('n',identity) in index.lexical.postings.get(anchor,{}) for anchor in anchors)
                       for ref in index.nodes[identity].get('evidence_ids',[])}
        direct = [i for i in candidates if token_sets[i]&anchors]
        # Prefer the concept in the actual excerpt over a model's interpretation.
        # Graph-only hints remain useful when no raw excerpt names the concept.
        candidates = direct or [i for i in candidates if i in interpreted][:3]
    if candidates:
        floor = max(es[i] for i in candidates)*.18
        candidates = [i for i in candidates if es[i]>=floor]
    def doc_id(i):
        e=index.evidence[i]
        return e.get('document_id') or e.get('source_url') or e.get('url') or i
    def publisher(i):
        e=index.evidence[i]
        return urlsplit(e.get('source_url') or e.get('url') or '').netloc
    def rank(i):
        e=index.evidence[i]
        relevance=es[i]*(1+.25*len((token_sets[i]&query_terms)-covered))/(1+.2*publishers[publisher(i)])
        # Date breaks equal relevance only; a newer irrelevant article never wins.
        return relevance,e.get('day',''),i
    while candidates and len(chosen)<RETRIEVAL_EVIDENCE_LIMIT:
        identity=max(candidates,key=rank)
        candidates.remove(identity)
        if doc_id(identity) in documents:continue
        chosen.append(identity);documents.add(doc_id(identity))
        publishers[publisher(identity)]+=1
        covered |= token_sets[identity]&query_terms
    refs=set(chosen)
    node_scores=dict(ns)
    for ref in chosen:
        for identity in index.refs[ref]:
            if scope is None or identity in scope:
                node_scores[identity]=node_scores.get(identity,0)+es[ref]/max(1,len(index.refs[ref]))
    eligible = {i for ref in chosen for i in index.refs[ref] if scope is None or i in scope}
    selected = sorted(eligible,key=lambda i:(i not in requested,-node_scores.get(i,0),i))[:RETRIEVAL_NODE_LIMIT]
    selected_set=set(selected)
    nodes = []
    for identity in selected:
        n=deepcopy(index.nodes[identity]);n['evidence_ids']=[r for r in n.get('evidence_ids',[]) if r in refs]
        nodes.append(n)
    edges=[]
    for edge in index.edges:
        attached=[r for r in edge.get('evidence_ids',[]) if r in refs]
        if edge['source'] in selected_set and edge['target'] in selected_set and attached:
            e=deepcopy(edge);e['evidence_ids']=attached;edges.append(e)
    edges.sort(key=lambda e:(-sum(es.get(r,0) for r in e['evidence_ids']),e['id']))
    return {'nodes':nodes,'edges':edges[:RETRIEVAL_EDGE_LIMIT],
            'evidence':[deepcopy(index.evidence[i]) for i in chosen], 'no_hits':not chosen,
            'limitations': ['현재 필터의 검토 자료에서 원문 관련도·한영 등록 표현·그래프 연결로 검색했습니다. 출처가 다른 문서도 독립 확인으로 단정하지 않습니다.'] if chosen else ['질문과 관련된 저장 근거를 찾지 못했습니다.'],
            'retrieval':{'method':VERSION,'index_cache_hit':hit,'candidate_documents':len(es),
                         'unique_documents':len(documents),'milliseconds':round((time.monotonic()-started)*1000,2)}}
