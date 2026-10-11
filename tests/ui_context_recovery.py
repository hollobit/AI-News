"""Exercise delayed analysis, transport recovery and saved-job restoration without model calls."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.runtime/browsers')
from playwright.async_api import async_playwright, expect


async def main():
    identity = 'a' * 32
    question = 'Physical AI 벤치마크의 비교 기준은?'
    result = dict(answer='평가 환경의 차이에 따라 비교 범위가 달라집니다.',
        verification={'method': 'independent_evidence_review'},
        claims=[dict(text='검토된 관계 분석', kind='connection', evidence_ids=['e'])],
        evidence=[dict(id='e', title='공식 원자료', text='제공된 근거', source_url='https://example.org/a')])
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 390, 'height': 844})
        await page.clock.install()
        errors, posts, polls = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        responses = ['preparing', 503, 'offline', 'running', 'complete']
        dossier_calls = []

        async def route(r):
            path = urlparse(r.request.url).path
            if path == '/api/context':
                dossier_calls.append(1)
                if len(dossier_calls) == 2:
                    await r.fulfill(status=503, json={'error': '저장소 갱신 중'})
                else:
                    await r.fulfill(json=dict(status='ready', revision='v1', groups={}, labels={}))
            elif path == '/api/graph/ask':
                posts.append(r.request.post_data_json)
                await r.fulfill(json=dict(phase='preparing', evidence=[], job=dict(id=identity)))
            elif path.startswith('/api/graph/answers/'):
                polls.append(path)
                state = responses.pop(0) if responses else 'complete'
                if state == 'offline':
                    await r.abort('internetdisconnected')
                elif isinstance(state, int):
                    await r.fulfill(status=state, json={'error': '연결 지연'})
                elif isinstance(state, dict):
                    await r.fulfill(json=state)
                else:
                    await r.fulfill(json=dict(status=state, error='근거가 갱신되었습니다.' if state == 'stale' else '',
                        result=result if state in ('running', 'complete') else {}))
            else:
                file = ROOT / 'static' / ('context.html' if path == '/context' else path.lstrip('/'))
                if file.is_file():
                    await r.fulfill(path=str(file))
                else:
                    await r.fulfill(status=404, body='missing')

        await page.route('http://test.local/**', route)
        await page.goto('http://test.local/context')
        await expect(page.locator('#context-example')).to_have_count(0)
        await expect(page.locator('#query-example')).to_contain_text('예시 질문')
        await expect(page.locator('#query')).to_be_empty()
        example=await page.locator('#query').get_attribute('placeholder')
        await expect(page.locator('#query-use-example')).to_have_count(0)
        for key in ('ArrowUp','ArrowDown','ArrowLeft','ArrowRight'):
            await page.locator('#query').fill('')
            await page.locator('#query').press(key)
            await expect(page.locator('#query')).to_have_value(example)
        await page.locator('#query').fill('편집 중인 질문')
        await page.locator('#query').press('ArrowLeft')
        await expect(page.locator('#query')).to_have_value('편집 중인 질문')
        await page.locator('#query').fill('')
        await page.locator('#query').press('Shift+ArrowRight')
        await expect(page.locator('#query')).to_be_empty()
        await page.locator('#query').dispatch_event('keydown',dict(key='ArrowRight',isComposing=True))
        await expect(page.locator('#query')).to_be_empty()
        assert not posts
        await page.locator('#query').fill(question)
        await expect(page.locator('#query')).to_have_value(question)
        await page.locator('#context-ask-button').click()
        await expect(page.locator('#answer-status')).to_contain_text('연결 색인을 준비')
        await expect(page.locator('#answer-detail')).to_contain_text('수 분')
        await expect(page.locator('#context-answer')).to_be_empty()
        await expect(page.locator('[aria-current="step"]')).to_have_text('근거 준비')
        await page.clock.run_for(3100)
        await expect(page.locator('#answer-status')).to_contain_text('기존 분석 결과를 다시 조회')
        await page.clock.run_for(2100)
        await expect(page.locator('#answer-detail')).to_contain_text('4초 후')
        await page.clock.run_for(4100)
        await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 중')
        await expect(page.locator('[aria-current="step"]')).to_have_text('종합 분석·독립 검토')
        await expect(page.locator('#context-answer')).to_contain_text('최종 종합 답변이 아닙니다')
        await expect(page.locator('#context-answer')).not_to_contain_text('검토된 관계 분석')
        # A bfcache restoration resumes GET polling, without creating another analysis.
        await page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide')); dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 완료')
        await expect(page.locator('#context-answer')).to_contain_text('검토된 관계 분석')
        assert len(posts) == 1 and len(polls) == 5
        assert len(dossier_calls) >= 3
        await page.reload()
        await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 완료')
        assert len(posts) == 1
        # Stale results must not be displayed as current, and retry uses the submitted question.
        responses[:] = ['stale']
        await page.reload()
        await expect(page.locator('#answer-retry')).to_be_visible()
        await expect(page.locator('#context-answer')).to_be_empty()
        await page.locator('#query').fill('제출하지 않은 다른 질문')
        await page.locator('#answer-retry').click()
        await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 완료')
        assert posts[-1]['question'] == question and len(posts) == 2
        # Stop bounded automatic retries, then manually resume the same saved job.
        responses[:] = [503] * 9
        await page.reload()
        for failure in range(1, 9):
            await expect(page.locator('#answer-detail')).to_contain_text(f'{min(15, 2 * failure)}초 후')
            await page.clock.run_for(min(15000, 2000 * failure) + 100)
        await expect(page.locator('#answer-resume')).to_be_visible()
        await page.locator('#answer-resume').click()
        await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 완료')
        assert len(posts) == 2
        # Authentication is actionable, never endlessly retried as a network error.
        responses[:] = [401]
        await page.reload()
        await expect(page.locator('#answer-login')).to_be_visible()
        assert 'answer=' + identity in await page.locator('#answer-login').get_attribute('href')
        count = len(polls)
        await page.clock.run_for(60000)
        assert len(polls) == count and len(posts) == 2
        # Server-recorded progress survives reload; a review in progress is not a published answer.
        labels=['검색 근거 준비','질문 해석·검색 계획','관계 분석·답변 작성','독립 근거 검토','최신 근거 확인·완료']
        progress=dict(completed=3,total=5,percent=60,elapsed_seconds=74,remaining_seconds=26,
            estimate_samples=2,estimate_overdue=False,
            stages=[dict(id=str(i),label=label,status='complete' if i<3 else 'running' if i==3 else 'pending',seconds=12 if i<4 else None) for i,label in enumerate(labels)])
        responses[:]=[dict(status='running',result={'evidence':result['evidence']},progress=progress)]
        await page.reload()
        await expect(page.locator('#answer-count')).to_have_text('완료 단계 3/5 · 60% · 전체 경과 74초')
        await expect(page.locator('#answer-eta')).to_contain_text('약 26초')
        await expect(page.locator('#answer-progress [aria-current="step"]')).to_contain_text('독립 근거 검토')
        await expect(page.locator('#context-answer')).not_to_contain_text('검토된 관계 분석')
        assert await page.locator('#answer-meter').evaluate('(el)=>el.value')==60
        await page.clock.run_for(3100)
        await expect(page.locator('#context-answer')).to_contain_text('검토된 관계 분석')
        assert len(posts)==2
        await page.evaluate("localStorage.setItem('news.context.recent-queries.v1',JSON.stringify(Array.from({length:12},(_,i)=>({question:'저장 질의 '+i,used_at:100+i}))))")
        await page.reload()
        await expect(page.locator('#query-history-items button')).to_have_count(10)
        await expect(page.locator('#query-history-items button').first).to_have_text('저장 질의 11')
        await page.locator('#query-history-items button').nth(2).click()
        await expect(page.locator('#query')).to_have_value('저장 질의 9')
        assert len(posts)==2
        await page.locator('#context-ask-button').click()
        await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 완료')
        await expect(page.locator('#query-history-items button')).to_have_count(10)
        await expect(page.locator('#query-history-items button').first).to_have_text('저장 질의 9')
        await page.reload()
        await expect(page.locator('#query-history-items button').first).to_have_text('저장 질의 9')
        assert len(posts)==3
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors, errors
        print(json.dumps(dict(preparation_visible=True, transport_recovery=True, bfcache_resume=True,
            reload_without_duplicate=True, stale_retry=True, bounded_retry=True, login_expiry=True, errors=errors)))
        await browser.close()


asyncio.run(main())
