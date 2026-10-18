import threading
from collections import Counter
from concurrent.futures import CancelledError, ThreadPoolExecutor
from unittest.mock import patch

import pytest

import evidence_search as search
from task_lifecycle import cancellation_scope


def test_exact_fields_are_reused_without_sharing_mutable_index_state():
    cache = search._FieldCountsCache()
    rows = {'a': [('중국 반도체 정책', 3), ('중국 중국 반도체', 1)]}
    with patch.object(search, '_FIELD_COUNTS', cache), patch.object(search, 'terms', wraps=search.terms) as tokenize:
        first = search.LexicalIndex(rows)
        assert tokenize.call_count == 2
        before = dict(first.search('중국 반도체'))
        tokenize.reset_mock()
        second = search.LexicalIndex(rows)
        assert tokenize.call_count == 0
        assert second.search('중국 반도체') == before
        first.postings['중국'].clear()
        third = search.LexicalIndex(rows)
        assert third.search('중국 반도체') == before


def test_changed_text_boost_and_corpus_recompute_counts_and_ranking():
    cache = search._FieldCountsCache()
    repeated = 'semiconductor ' * 10
    with patch.object(search, '_FIELD_COUNTS', cache):
        before = search.LexicalIndex({'a': [(repeated, 3)]})
        after = search.LexicalIndex({'a': [(repeated, 1)], 'b': [('regulation', 3)]})
        assert before.postings['semiconductor']['a'] == 12
        assert after.postings['semiconductor']['a'] == 4
        assert before.search('semiconductor') != after.search('semiconductor')
        changed = search.LexicalIndex({'a': [('regulation', 3)]})
        assert changed.search('semiconductor') == {}
        assert set(changed.search('규제')) == {'a'}
        assert search.LexicalIndex({}).search('semiconductor') == {}


def test_cached_tokens_preserve_normalization_aliases_and_duplicates():
    cache = search._FieldCountsCache()
    for value in ('ＡＩ 반도체의 regulation regulation', '미국과 중국의 수출통제', '', None, 2026):
        expected = tuple(Counter(search.terms(value)).items())
        assert cache.get(value) == expected
        assert cache.get(value) == expected


def test_lru_and_byte_budgets_evict_without_changing_results():
    cache = search._FieldCountsCache(max_entries=2)
    first = cache.get('semiconductor')
    cache.get('regulation')
    cache.get('semiconductor')
    cache.get('sovereignty')
    assert list(cache.entries) == ['semiconductor', 'sovereignty']
    assert cache.get('semiconductor') == first
    one_size = cache.entries['semiconductor'][1]
    limited = search._FieldCountsCache(max_bytes=one_size)
    limited.get('semiconductor')
    limited.get('regulation')
    assert limited.bytes <= one_size
    assert len(limited.entries) <= 1
    assert limited.bytes == sum(size for _, size in limited.entries.values())
    oversized = search._FieldCountsCache(max_bytes=1)
    assert oversized.get('semiconductor') == first
    assert oversized.bytes == 0 and not oversized.entries


def test_long_fields_are_searchable_without_being_retained():
    cache = search._FieldCountsCache(max_chars=5)
    with patch.object(search, '_FIELD_COUNTS', cache):
        index = search.LexicalIndex({'a': [('semiconductor regulation', 1)]})
        assert set(index.search('반도체')) == {'a'}
    assert not cache.entries


def test_cache_hits_honor_cancellation_and_interrupted_work_is_not_retained():
    cache = search._FieldCountsCache()
    cache.get('semiconductor')
    event = threading.Event()
    with pytest.raises(CancelledError):
        with cancellation_scope(event):
            event.set()
            cache.get('semiconductor')
    with patch.object(search, 'terms', side_effect=CancelledError):
        with pytest.raises(CancelledError):
            cache.get('regulation')
    assert 'regulation' not in cache.entries


def test_concurrent_same_text_misses_do_not_inflate_retained_bytes():
    cache = search._FieldCountsCache()
    barrier = threading.Barrier(4)
    original = search.terms

    def tokenize(value):
        barrier.wait(timeout=5)
        return original(value)

    with patch.object(search, 'terms', side_effect=tokenize), ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(cache.get, ['중국 반도체'] * 4))
    assert all(result == results[0] for result in results)
    assert len(cache.entries) == 1
    assert cache.bytes == cache.entries['중국 반도체'][1]
