"""Bounded literal AND/OR query language shared with observatory-search.js."""
import unicodedata


class QueryError(ValueError):
    """Safe user-facing syntax diagnostic."""


def normalize(value):
    return unicodedata.normalize('NFKC', str(value)).lower()


def parse(value):
    text = value.strip()
    if len(text) > 256:
        raise QueryError('검색어는 256자 이내로 입력해 주세요.')
    tokens = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == '|':
            tokens.append('OR'); i += 1
            continue
        negative = text[i] == '-'
        if negative: i += 1
        quoted = i < len(text) and text[i] == '"'
        word = ''
        if quoted:
            i += 1
            while i < len(text) and text[i] != '"':
                if text[i] == '\\' and i + 1 < len(text): i += 1
                word += text[i]; i += 1
            if i == len(text): raise QueryError('따옴표를 닫아 주세요.')
            i += 1
            if i < len(text) and not (text[i].isspace() or text[i] == '|'):
                raise QueryError('구문 사이에 공백을 넣어 주세요.')
        else:
            while i < len(text) and not (text[i].isspace() or text[i] == '|'):
                word += text[i]; i += 1
        if not word: raise QueryError('검색할 키워드를 입력해 주세요.')
        tokens.append(word.upper() if not negative and not quoted and word.upper() in {'AND', 'OR'}
                      else {'value': normalize(word), 'negative': negative})
    groups = [[]]; expected = True; count = 0
    for token in tokens:
        if isinstance(token, str):
            if expected: raise QueryError('AND 또는 OR 앞뒤에 키워드를 입력해 주세요.')
            expected = True
            if token == 'OR': groups.append([])
        else:
            groups[-1].append(token); expected = False; count += 1
    if tokens and expected: raise QueryError('AND 또는 OR 뒤에 키워드를 입력해 주세요.')
    if count > 20: raise QueryError('키워드는 20개 이내로 입력해 주세요.')
    return groups if tokens else []


def matches(groups, texts):
    values = [normalize(v) for v in texts]
    return not groups or any(all(any(t['value'] in v for v in values) != t['negative']
                                for t in group) for group in groups)


def snippets(groups, fields):
    terms = {t['value'] for g in groups for t in g if not t['negative']}
    result = []
    for label, text in fields:
        normalized = normalize(text)
        positions = [normalized.find(t) for t in terms if t in normalized]
        if not positions: continue
        start = max(0, min(positions) - 65)
        result.append({'field': label, 'text': ('…' if start else '') + str(text)[start:start+260] +
                       ('…' if start+260 < len(str(text)) else '')})
    return result[:5]
