"""A stale public tab must resolve the current origin before opening a live menu."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.runtime/browsers'))
from playwright.async_api import async_playwright, expect


async def main():
    origin = 'https://old-site.trycloudflare.com'
    fail = False
    hits = 0
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        async def route(r):
            nonlocal hits
            u = urlparse(r.request.url)
            if u.hostname != 'public.test':
                await r.fulfill(content_type='text/html', body='<main id="arrived">전략 검토</main>')
            elif u.path.endswith('remote-access.json'):
                hits += 1
                await r.fulfill(status=503, json={}) if fail else await r.fulfill(json={'origin': origin, 'mode': 'temporary'})
            else:
                name = 'remote-context.html' if u.path.endswith('context.html') else u.path.rsplit('/', 1)[-1]
                f = ROOT / 'static' / name
                await r.fulfill(path=str(f)) if f.is_file() else await r.fulfill(status=404, body='missing')
        await page.route('**/*', route)
        url = 'https://public.test/context.html?destination=intelligence&q=AI&date=2026-10-15'
        await page.goto(url)
        await expect(page.locator('#remote-open')).to_have_attribute('href', origin + '/intelligence?q=AI&ws_date=2026-10-15')
        origin = 'https://new-site.trycloudflare.com'
        before = hits
        await page.locator('#remote-open').click()
        await expect(page.locator('#arrived')).to_be_visible()
        assert hits == before + 1 and page.url.startswith(origin + '/intelligence?')
        assert parse_qs(urlparse(page.url).query) == {'q': ['AI'], 'ws_date': ['2026-10-15']}
        await page.goto(url)
        await expect(page.locator('#remote-open')).to_be_visible()
        fail = True
        await page.locator('#remote-open').click()
        await expect(page.locator('#remote-status')).to_contain_text('접속 정보를 불러올 수 없습니다')
        await expect(page.locator('#remote-refresh')).to_be_enabled()
        await expect(page.locator('#remote-open')).to_be_hidden()
        assert page.url == url
        fail = False
        origin = 'https://api.trycloudflare.com'
        await page.locator('#remote-refresh').click()
        await expect(page.locator('#remote-status')).to_contain_text('접속 주소를 확인')
        await expect(page.locator('#remote-open')).to_be_hidden()
        origin = 'https://restored-site.trycloudflare.com'
        await page.locator('#remote-refresh').click()
        await expect(page.locator('#remote-open')).to_be_visible()
        await page.locator('#remote-query').fill('의료 & AI')
        await page.locator('#remote-query').press('Enter')
        await expect(page.locator('#arrived')).to_be_visible()
        assert page.url.startswith(origin + '/intelligence?')
        assert parse_qs(urlparse(page.url).query)['q'] == ['의료 & AI']
        assert not errors, errors
        print(json.dumps(dict(latest_origin_on_click=True,failed_refresh_stays_on_page=True,reserved_origin_rejected=True,query_preserved=True,errors=errors)))
        await browser.close()

asyncio.run(main())
