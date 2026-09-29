"""Read-only exploration: state, focus, scenario paths and bounded playback."""
import asyncio
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect

async def main():
    errors=[];requests=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1080,'height':900},reduced_motion='reduce')
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url) if r.url.startswith(('http:','https:')) else None)
        url=(ROOT/'docs/news-architecture.html').as_uri()
        await page.goto(url)
        review=page.locator('[data-step="review"]')
        await review.click();await expect(page).to_have_url(__import__('re').compile('step=review'))
        await page.reload();await expect(review).to_have_attribute('aria-pressed','true')
        await page.locator('[data-step="analysis"]').click()
        await page.go_back();await expect(review).to_have_attribute('aria-pressed','true')
        await page.go_forward();await expect(page.locator('[data-step="analysis"]')).to_have_attribute('aria-pressed','true')
        await review.focus();await page.set_viewport_size({'width':1040,'height':900});await page.wait_for_timeout(100)
        await expect(review).to_be_focused()
        await page.set_viewport_size({'width':390,'height':844});await page.wait_for_timeout(100);await expect(review).to_be_focused()
        await page.locator('[data-step="receive"]').click()
        assert await page.evaluate("document.querySelector('[data-step=receive]').nextElementSibling.classList.contains('cycle-note')")
        assert await page.locator('.cycle-note h2').evaluate('(e)=>e.getBoundingClientRect().bottom<innerHeight')
        await page.locator('.cycle-note summary').focus()
        await page.set_viewport_size({'width':400,'height':844});await page.wait_for_timeout(100)
        await expect(page.locator('.cycle-note summary')).to_be_focused()
        await page.screenshot(path=str(ROOT/'.runtime/verification/architecture-mobile-interaction.png'))
        await page.set_viewport_size({'width':1080,'height':900})
        await page.locator('#stage-picker').select_option('review')
        await page.locator('[data-view="architecture"]').click()
        await expect(page.locator('[data-node="review"]')).to_have_attribute('aria-pressed','true')
        node=page.locator('[data-node="ledger"]');await node.focus();await page.keyboard.press('Enter')
        await expect(page.locator('#stage-picker')).to_have_value('ledger')
        await expect(page.locator('[data-node="ledger"]')).to_be_focused()
        await page.set_viewport_size({'width':1020,'height':900});await page.wait_for_timeout(100)
        await expect(page.locator('[data-node="ledger"]')).to_be_focused()
        await page.locator('[data-view="cycle"]').click();await expect(page.locator('[data-step="ledger"]')).to_have_attribute('aria-pressed','true')
        for scenario in ('duplicate','changed','limited','paused','normal'):
            await page.locator('#scenario-picker').select_option(scenario)
            await page.reload();await expect(page.locator('#scenario-picker')).to_have_value(scenario)
            if scenario=='duplicate':
                await expect(page.locator('[data-step="analysis"]')).to_have_attribute('data-on-route','false')
                assert not await page.locator('.cycle-wires [data-to="analysis"]').count()
            if scenario in ('limited','paused'):
                assert not await page.locator('.cycle-wires [data-to="publish"]').count()
        await page.clock.install()
        await page.locator('#scenario-picker').select_option('limited')
        await page.locator('#explain-play').click()
        for _ in range(5):await page.clock.fast_forward(2800)
        await expect(page.locator('#stage-picker')).to_have_value('analysis')
        await expect(page.locator('#explain-play')).to_have_attribute('aria-pressed','false')
        await expect(page.locator('.cycle-note')).to_contain_text('여기서 대기')
        await page.locator('#explain-play').click();await page.clock.fast_forward(2800)
        await page.locator('#explain-play').click()
        stopped=await page.locator('#stage-picker').input_value();await page.clock.fast_forward(10000)
        await expect(page.locator('#stage-picker')).to_have_value(stopped)
        await page.goto(url+'#view=invalid&step=missing&scenario=unknown')
        await expect(page.locator('#scenario-picker')).to_have_value('normal')
        await expect(page.locator('[data-step="receive"]')).to_have_attribute('aria-pressed','true')
        await page.wait_for_timeout(100)
        await page.screenshot(path=str(ROOT/'.runtime/verification/architecture-interactions.png'),full_page=True)
        assert not errors,errors
        assert not requests,requests
        print('URL reload/back/forward, mobile detail, resize focus, linked views, five scenarios, playback stop, reduced motion, offline: passed')
        await browser.close()
asyncio.run(main())
