"""Select source-backed keyword IDs; materialize verbatim quotes in code."""
import copy
import hashlib
import re


def candidates(snapshot, prepared):
    found = {}
    for term in prepared['keywords'][:8]:
        label, surface = term['label'], term.get('surface') or term['label']
        pattern = re.escape(surface)
        if surface.isascii():
            pattern = r'(?<![A-Za-z0-9_])' + pattern + r'(?![A-Za-z0-9_])'
        if any(re.search(pattern, e['text']) for e in snapshot['evidence']):
            key = 'kw_' + hashlib.sha256((label + '\0' + surface).encode()).hexdigest()[:16]
            found[key] = {'label': label, 'source_quote': surface}
    return found


def generation_schema(base, citation_ids=None):
    schema = copy.deepcopy(base)
    schema['properties']['documents']['items']['properties']['keywords']['items'] = {
        'type': 'object', 'properties': {'citation_id': {'type': 'string'}},
        'required': ['citation_id'], 'additionalProperties': False}
    if citation_ids is not None:
        keywords = schema['properties']['documents']['items']['properties']['keywords']
        if citation_ids:
            keywords['items']['properties']['citation_id']['enum'] = sorted(set(citation_ids))
        else:
            keywords['maxItems'] = 0
    return schema


def materialize(record, allowed):
    result = copy.deepcopy(record)
    resolved = []
    for keyword in result.get('keywords', []):
        if isinstance(keyword, dict) and set(keyword) == {'citation_id'}:
            if keyword['citation_id'] not in allowed:
                raise ValueError('현재 문서에 없는 키워드 인용 ID')
            resolved.append(dict(allowed[keyword['citation_id']]))
        else:
            # Old checkpoints retain the original strict validator, never get repaired silently.
            resolved.append(keyword)
    result['keywords'] = resolved
    return result
