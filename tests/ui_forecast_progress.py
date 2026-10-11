"""Forecast stages, measured waiting time and polling recovery without model calls."""
import asyncio
import copy
import json
import os
from pathlib import Path
from urllib.parse import urlparse, urlencode

ROOT = Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright, expect


async def main():
    identity = 'analysis:' + 'a'*32
    state = dict(id=identity, title='Physical AI 전망', status='queued', result={}, progress=dict(
        recorded=True, percent=0, completed=0, total=4, elapsed_seconds=0, remaining_seconds=None,
        estimate_samples=5, estimate_basis=None, estimate_overdue=False, typical_seconds=100, queue_ahead=2,
        stages=[dict(id=s, label=label, status='running' if s=='queue' else 'pending', seconds=0 if s=='queue' else None)
                for s,label in [('queue','실행 대기'),('generation','조건부 전망 작성'),('review','독립 근거·확률 검토'),('finalize','검토 결과 확인·저장')]]))
    errors = []; posts = []; polls = []
    transport = dict(status=200)
    posted = asyncio.Event(); accept = asyncio.Event()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1440,'height':1000})
        await page.clock.install()
        page.on('pageerror', lambda e:errors.append(str(e)))
        async def route(r):
            path = urlparse(r.request.url).path
            if r.request.method == 'POST':
                posts.append(path)
                assert path == '/api/intelligence/forecasts'
                posted.set()
                await accept.wait()
                await r.fulfill(json=dict(run=copy.deepcopy(state)))
            elif path == '/api/intelligence':
                if 'view=forecasts' in r.request.url:
                    await r.fulfill(json=dict(items=[dict(state,created_at='2026-10-11')]))
                else:
                    polls.append(path)
                    await r.fulfill(status=transport['status'],json=dict(item=copy.deepcopy(state)) if transport['status']==200 else dict(error='접속 실패'))
            elif path == '/api/context':
                await r.fulfill(json=dict(status='ready',revision='v1',labels={},groups={}))
            else:
                file = ROOT/'static'/('context.html' if path=='/context' else path.lstrip('/'))
                if file.is_file():await r.fulfill(path=str(file))
                else:await r.fulfill(status=404,body='missing')
        await page.route('http://test.local/**',route)
        await page.goto('http://test.local/context?' + urlencode(dict(q=state['title'])))
        await page.locator('#forecast-start').click()
        await asyncio.wait_for(posted.wait(), 3)
        await expect(page.locator('#forecast-eta')).to_contain_text('근거 준비 후')
        assert await page.locator('#forecast-meter').get_attribute('value') is None
        await page.clock.run_for(2100)
        await expect(page.locator('#forecast-count')).to_contain_text('요청 경과 2초')
        await expect(page.locator('#forecast-history')).to_be_disabled()
        accept.set()
        await expect(page.locator('#forecast-eta')).to_contain_text('앞선 작업 2건')
        await expect(page.locator('#forecast-start')).to_be_disabled()
        await expect(page.locator('#forecast-meter')).to_have_attribute('value','0')
        state['status'] = 'running'
        state['progress'].update(percent=25,completed=1,elapsed_seconds=10,remaining_seconds=90,estimate_basis='total')
        state['progress']['stages'][0].update(status='complete',seconds=10)
        state['progress']['stages'][1].update(status='running',seconds=0)
        await page.clock.run_for(1100)
        await expect(page.locator('#forecast-status')).to_contain_text('조건부 전망 작성 진행 중')
        await expect(page.locator('#forecast-eta')).to_contain_text('최근 5건 전체 소요 실측')
        await page.clock.run_for(1000)
        await expect(page.locator('#forecast-count')).to_contain_text('11초')
        await expect(page.locator('#forecast-meter')).to_have_attribute('value','25')
        transport['status'] = 503
        await page.clock.run_for(3100)
        await expect(page.locator('#forecast-status')).to_contain_text('자동으로 다시 확인')
        await expect(page.locator('#forecast-resume')).to_be_visible()
        await expect(page.locator('#forecast-start')).to_be_disabled()
        transport['status'] = 200
        state['progress'].update(percent=50,completed=2,elapsed_seconds=90,remaining_seconds=30,estimate_basis='stages')
        state['progress']['stages'][1].update(status='complete',seconds=80)
        state['progress']['stages'][2].update(status='running',seconds=0)
        await page.clock.run_for(3100)
        await expect(page.locator('#forecast-status')).to_contain_text('독립 근거·확률 검토 진행 중')
        await expect(page.locator('#forecast-meter')).to_have_attribute('value','50')
        await expect(page.locator('#forecast-eta')).to_contain_text('단계별 실측')
        state['progress']['estimate_overdue'] = True
        await page.clock.run_for(3100)
        await expect(page.locator('#forecast-eta')).to_contain_text('예상보다 오래')
        await page.reload()
        await expect(page.locator('#forecast-status')).to_contain_text('독립 근거·확률 검토 진행 중')
        await expect(page.locator('#forecast-start')).to_be_disabled()
        assert len(posts) == 1
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        transport['status'] = 401
        await page.clock.run_for(3100)
        await expect(page.locator('#forecast-login')).to_be_visible()
        count = len(polls)
        await page.clock.run_for(10000)
        assert len(polls) == count
        transport['status'] = 200
        state.update(status='failed',error='TimeoutError — 분석 실행을 완료하지 못했습니다.')
        state['progress']['stages'][2].update(status='stopped',seconds=240)
        state['progress']['elapsed_seconds'] = 330
        await page.locator('#forecast-resume').click()
        await expect(page.locator('#forecast-status')).to_contain_text('실행 실패')
        await expect(page.locator('#forecast-meter')).to_have_attribute('value','50')
        await expect(page.locator('#forecast-eta')).to_contain_text('처리가 종료')
        text = await page.locator('#forecast-count').inner_text()
        count = len(polls)
        await page.clock.run_for(20000)
        assert await page.locator('#forecast-count').inner_text() == text and len(polls) == count
        state.update(status='needs_review',error='근거가 변경되었습니다.',result=dict(verified=False))
        await page.reload()
        await expect(page.locator('#forecast-results')).to_contain_text('예측은 표시하지 않습니다')
        await expect(page.locator('#forecast-status')).to_contain_text('근거가 변경')
        state.update(status='complete',error='',result=dict(verified=True,as_of='2026-10-11',horizon_days=180,
            basis='주관적 추정',report=dict(summary='검토된 전망',comparisons=[],limitations=[])))
        state['progress'].update(percent=100,completed=4)
        for s in state['progress']['stages']:s.update(status='complete',seconds=10)
        await page.reload()
        await expect(page.locator('#forecast-results')).to_contain_text('검토된 전망')
        await expect(page.locator('#forecast-meter')).to_have_attribute('value','100')
        # Saved history stops polling and selecting it restores the same job URL.
        state['status'] = 'running'
        await page.reload()
        await expect(page.locator('#forecast-start')).to_be_disabled()
        await page.locator('#forecast-history').click()
        await expect(page.locator('#forecast-status')).to_contain_text('저장된 전망 ·')
        count = len(polls)
        await page.clock.run_for(15000)
        assert len(polls) == count
        await page.locator('#forecast-results > button').click()
        await expect(page.locator('#forecast-start')).to_be_disabled()
        assert 'forecast=' in page.url
        # A bfcache return resumes GET polling, with no new forecast POST.
        count = len(polls)
        await page.evaluate("dispatchEvent(new PageTransitionEvent('pagehide')); dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
        await expect(page.locator('#forecast-meter')).to_be_visible()
        await page.clock.run_for(1000)
        assert len(polls) > count and len(posts) == 1 and not errors, (polls,posts,errors)
        print(json.dumps(dict(stages=True,eta=True,recovery=True,refresh=True,terminal=True,history=True,
                              mobile=[390,320],model_posts=0,simulated_posts=len(posts),errors=errors),ensure_ascii=False))
        await browser.close()


asyncio.run(main())
