"""Optional Jev filtering of keyword candidates; never edits facts or approves analysis."""
from decision_journal import application, context, safely
from decision_tasks import choice
from system_one import config, decide

VERSION = 'baseline-keywords-v1'


def filter_candidates(path, run_id, snapshots, options):
    settings = config()
    if not settings.get('applies_baseline_keywords'):
        return options, {'method': 'deterministic', 'removed': 0}
    # Keywords are optional. Only ambiguous multiword noun phrases need this
    # additional classification; code already validates verbatim source spans.
    entries = [(identity, key, value) for identity, choices in options.items()
               for key, value in choices.items() if ' ' in value['label']]
    if not entries or len(entries) > 16:
        return options, {'method': 'deterministic', 'removed': 0}
    documents = {identity: {'title': snapshots[identity]['title'], 'evidence': snapshots[identity]['evidence']}
                 for identity, _, _ in entries}
    state = {'version': VERSION, 'documents': documents,
             'candidates': [dict(document_id=i, label=v['label'], source_quote=v['source_quote']) for i, _, v in entries]}
    questions = {f'keyword_{n}': choice(
        f'Inspect candidates[{n}] only against its own document. Is the label a complete, useful keyword? '
        'A standalone named entity or meaningful shorter concept is valid. Reject only a phrase truncated mid-name '
        'or mid-concept, such as omitting a model version or ending in a dangling modifier. Do not decide factual truth.',
        {'keep': 'Complete meaningful keyword', 'fragment': 'Clearly truncated or dangling phrase', 'unknown': 'Uncertain'})
        for n in range(len(entries))}
    evidence = [e for s in snapshots.values() for e in s['evidence']]
    result = decide(path, 'baseline_keywords', state, questions,
                    subject=context('baseline', run_id, '기본 분석 키워드 후보 점검', evidence))
    filtered = {identity: dict(choices) for identity, choices in options.items()}
    removed = 0
    if result.get('status') == 'complete':
        from graph_plan_router import confident
        for n, (identity, key, _) in enumerate(entries):
            answer = result.get('answers', {}).get(f'keyword_{n}', {})
            if confident(answer, 'fragment') and answer.get('confidence', 0) >= .95 and answer.get('probabilities', {}).get('fragment', 0) >= .95:
                del filtered[identity][key]
                removed += 1
    reason = 'fragment_candidates_removed' if removed else result.get('status', 'unavailable')
    safely(application, path, result.get('observation_id'), applied=bool(removed), reason=reason, scope='baseline_keywords')
    return filtered, dict(method='jev' if result.get('status') == 'complete' else 'deterministic',
                         status=result.get('status'), removed=removed, decision_ms=result.get('elapsed_ms'))
