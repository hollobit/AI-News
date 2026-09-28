import sqlite3
import pytest
from source_enrichment import SourceService, source_quality_error


def test_exact_privacy_shell_is_not_an_article():
    assert source_quality_error({'status':'fetched','title':'Your privacy choices','text':'Go to end'})
    assert not source_quality_error({'status':'fetched','title':'Reading instructions','text':'Go to end'})


@pytest.mark.parametrize('text', ['', '   ', 'Cookies must be enabled\nEnable cookies for pubmed.ncbi.nlm.nih.gov and reload this page to continue.', 'Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News.', '火山引擎\n正在进行安全检测...'])
def test_shell_is_not_article(text):
    assert source_quality_error({'status':'fetched','text':text})


def test_short_article_and_long_quote_remain_valid():
    assert not source_quality_error({'status':'fetched','text':'정부가 오늘 반도체 수출 규제를 발표했다.'})
    assert not source_quality_error({'status':'fetched','text':'Cookies must be enabled. '+ '보안 정책 분석 내용. '*100})


def test_fetch_stores_shell_failure(tmp_path):
    service=SourceService(tmp_path/'news.sqlite3',fetcher=lambda _: {'status':'fetched','text':'','title':'MSN'})
    try:
        result=service.fetch('https://example.com/story')
        assert result['status']=='failed'
        assert result['text']==''
        assert '빈 본문' in result['error']
        assert service.fetch('https://example.com/story')==result
    finally:
        service.close()


def test_original_publisher_url_is_fetched_but_cache_identity_stays_canonical(tmp_path):
    calls=[]
    actual='https://www.nature.com/articles/s41746-026-03095-2'
    canonical='https://nature.com/articles/s41746-026-03095-2'
    def fetch(url):
        calls.append(url)
        return {'status':'fetched','title':'Official article','text':'A useful article excerpt.'}
    service=SourceService(tmp_path/'source.sqlite',fetcher=fetch)
    try:
        result=service.fetch(actual)
        assert calls==[actual]
        assert result['url']==canonical and result['requested_url']==actual
        assert service.fetch(canonical)==result
        assert calls==[actual]
        service.fetch(actual,refresh=True)
        assert calls==[actual,actual]
        with sqlite3.connect(service.path) as db:
            assert db.execute('SELECT canonical_url FROM source_excerpts').fetchall()==[(canonical,)]
    finally: service.close()


def test_submit_preserves_original_variant_and_deduplicates_queue(tmp_path):
    actual='https://www.nature.com/articles/s41746-026-03095-2'
    canonical='https://nature.com/articles/s41746-026-03095-2'
    calls=[]
    service=SourceService(tmp_path/'queued.sqlite',fetcher=lambda url: calls.append(url) or {'status':'fetched','text':'Valid text'})
    try:
        assert service.submit([actual,canonical])=={'queued':1}
        assert list(service.jobs)==[canonical]
        service.jobs[canonical].result(timeout=3)
        assert calls==[actual]
    finally: service.close()
