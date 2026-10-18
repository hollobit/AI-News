"""Bounded, question-dependent source selection without additional model calls.

Lexical coverage is a retrieval diagnostic, never a claim of sufficient evidence.
Only the existing independent review can judge whether the answer is supported.
"""
from collections import Counter
import re
from urllib.parse import urlsplit

from evidence_search import terms, query_anchors, SUFFIX, STOP
from task_lifecycle import checkpoint

VERSION = 'adaptive-evidence-v1'
MAX_PASSAGES = 60
QUALIFICATION = re.compile(r'다만|그러나|미검증|한계|실패|상충|반박|제약|위험|'
                           r'\b(?:however|unless|limitation\w*|fail\w*|conflict\w*|risk\w*)\b', re.I)
REASONING_TERMS = set('연구 공통점 상충 의존 시사점 통찰 의미 핵심 종합 분석 결과 현황 영향 '
                      'research synthesis insight insights implication implications relationship relationships'.split())


def axes_for(question, plan, facets):
    """Explicit facets take precedence over overlapping generated search axes."""
    axes = [dict(label=words.split()[-1], pattern=pattern, words=set()) for pattern, words in facets]
    anchors = query_anchors(question)
    for label in plan.get('axes', []):
        if any(axis['pattern'] and axis['pattern'].search(label) for axis in axes):
            continue
        # Cross-source relationships are judged in synthesis, not proved by
        # finding the literal word "relationship" inside an original article.
        words = {word for word in set(terms(label)) - anchors
                 if SUFFIX.sub('', word) not in REASONING_TERMS | STOP}
        if words and not any(axis['words'] == words for axis in axes):
            axes.append(dict(label=label, pattern=None, words=words))
    return (axes or [dict(label=question, pattern=None, words=set(terms(question)))])[:8]


def budget_for(axes):
    count = max(1, len(axes))
    return dict(documents=min(40, 16 + 8 * (count - 1)),
                passages=min(MAX_PASSAGES, 20 + 12 * (count - 1)),
                characters=min(96000, 48000 + 16000 * (count - 1)),
                documents_per_axis=4 if count == 1 else 8)


def matches(item, axis):
    text = (item.get('title') or '') + ' ' + (item.get('text') or '')
    if axis['pattern']:
        return bool(axis['pattern'].search(text))
    return bool(set(terms(text)) & axis['words'])


def family(item):
    kind = item.get('source_kind', '')
    if 'paper' in kind:
        return 'paper'
    if kind in ('baseline', 'workflow', 'graph', 'telegram', 'stored_news_excerpt'):
        return 'news'
    return kind or 'unknown'


def select(groups, question, plan, facets, document, normalized):
    axes = axes_for(question, plan, facets)
    budget = budget_for(axes)
    # Preserve each query's rank while giving later query axes a turn.
    pool = {}
    for position in range(max((len(g['evidence']) for g in groups), default=0)):
        for group in groups:
            if position < len(group['evidence']):
                item = group['evidence'][position]
                if item.get('text'):
                    pool.setdefault(item['id'], item)
    candidates = list(pool.values())
    candidate_documents = len({document(e) for e in candidates})
    # Identical substantive text on several URLs is a repost cluster. Short
    # generic sentences and near matches with changed numbers are not merged.
    texts = {}; duplicate_ids = set()
    for item in candidates:
        text = normalized(item['text'])
        if len(text) >= 120:
            if text in texts and document(texts[text]) != document(item):
                duplicate_ids.add(item['id'])
            else:
                texts.setdefault(text, item)
    candidates = [e for e in candidates if e['id'] not in duplicate_ids]
    membership = {e['id']: {i for i, axis in enumerate(axes) if matches(e, axis)} for e in candidates}
    qualifications = {e['id'] for e in candidates if QUALIFICATION.search(e['text'])}
    selected = {}; documents = set(); chars = 0; publishers = Counter(); kinds = Counter()
    blocked = set()

    def add(item):
        nonlocal chars
        checkpoint()
        key = document(item)
        if key in documents or item['id'] in blocked:
            return False
        if len(documents) >= budget['documents'] or chars + len(item['text']) > budget['characters']:
            blocked.add(item['id'])
            return False
        selected[item['id']] = item; documents.add(key); chars += len(item['text'])
        publishers[urlsplit(item.get('source_url') or item.get('url') or '').netloc] += 1
        kinds[family(item)] += 1
        return True

    ranks = {e['id']: i for i, e in enumerate(candidates)}
    def choose(items):
        available = [e for e in items if document(e) not in documents and e['id'] not in blocked]
        # Relevance order stays dominant; source diversity breaks close ranks.
        available.sort(key=lambda e: (
            ranks[e['id']] // 8,
            publishers[urlsplit(e.get('source_url') or e.get('url') or '').netloc],
            kinds[family(e)], ranks[e['id']]))
        return next((e for e in available if add(e)), None)

    axis_candidates = [[e for e in candidates if i in membership[e['id']]] for i in range(len(axes))]
    # First reserve space for every axis before the initial broad selection.
    for items in axis_candidates:
        choose(items)
    initial_limit = min(16, budget['documents'])
    for item in candidates:
        if len(documents) >= initial_limit:
            break
        add(item)
    initial_documents = len(documents)
    reasons = set()
    # Expand only axes still below their retrieval target; never fill a missing
    # axis with unrelated documents just to reach a numeric quota.
    for _ in range(budget['documents_per_axis']):
        added = False
        for i, items in enumerate(axis_candidates):
            count = sum(i in membership[e['id']] for e in selected.values())
            if count < budget['documents_per_axis'] and choose(items):
                reasons.add('axis_coverage'); added = True
        if not added:
            break
    # Reserve actual qualification/risk clues and different material types.
    # The reviewer determines whether they really contradict the other sources.
    for i, items in enumerate(axis_candidates):
        if not any(i in membership[e['id']] and e['id'] in qualifications for e in selected.values()):
            if choose([e for e in items if e['id'] in qualifications]):
                reasons.add('qualification_clues')
        for kind in sorted({family(e) for e in items}):
            if not any(i in membership[e['id']] and family(e) == kind for e in selected.values()):
                if choose([e for e in items if family(e) == kind]):
                    reasons.add('source_types')
    stats = dict(policy=VERSION, candidate_documents=candidate_documents,
                 candidate_passages=len(pool), duplicate_passages=len(duplicate_ids),
                 initial_documents=initial_documents, expansion_reasons=sorted(reasons),
                 budget=budget, budget_limited=bool(blocked),
                 coverage_basis='lexical_retrieval_not_semantic_verification')
    return selected, stats, axes, candidates


def coverage(axes, candidates, evidence, document, budget):
    rows = []
    for axis in axes:
        available = {document(e) for e in candidates if matches(e, axis)}
        used = {document(e) for e in evidence if matches(e, axis)}
        # A fetched supplement can reveal a facet absent from its short index
        # excerpt. That document is also a candidate for the revealed facet.
        available |= used
        rows.append(dict(axis=axis['label'], candidate_documents=len(available),
                         selected_documents=len(used), target_documents=budget['documents_per_axis'],
                         status='no_candidates' if not available and not used else
                                'not_selected' if not used else
                                'below_target' if len(used) < budget['documents_per_axis'] else 'target_met'))
    return rows


def citation_counts(result, evidence, document):
    """Count corrected final citations, never all entries in the evidence bundle."""
    ids = {e['id'] for e in evidence}
    cited = {ref for claim in result.get('claims', []) for ref in claim.get('evidence_ids', []) if ref in ids}
    return dict(cited_passages=len(cited), cited_documents=len({document(e) for e in evidence if e['id'] in cited}))
