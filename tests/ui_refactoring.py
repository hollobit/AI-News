"""Read-only browser acceptance for split public data and live workspace roles."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.runtime/browsers')
from playwright.async_api import async_playwright, expect


async def main():
    folder = Path(os.environ.get('PUBLIC_SITE_DIR', str(ROOT / '.runtime/refactored-site'))).resolve()
    results, errors = {}, []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        requests = []

        async def route(request):
            path = urlparse(request.request.url).path.removeprefix('/AI-News/')
            requests.append(path)
            file = folder / path
            await request.fulfill(path=str(file)) if file.is_file() else await request.fulfill(status=404)

        await page.route('http://public.test/**', route)
        await page.goto('http://public.test/AI-News/index.html?view=strategy')
        await expect(page.locator('#content .card').first).to_be_visible(timeout=60000)
        await expect(page.locator('#dashboard-topics .card').first).to_be_visible()
        await expect(page.locator('#result-count')).to_contain_text('개 표시', timeout=60000)
        json_files = sorted({name for name in requests if name.endswith('.json')})
        total = sum((folder / name).stat().st_size for name in json_files)
        assert total <= 2 * 1024 * 1024, total
        assert not {'site.json', 'knowledge.json'} & set(requests)
        manifest = json.loads((folder / 'site-manifest.json').read_text())
        assert manifest['graph']['bootstrap'] not in requests
        assert manifest['news']['index'] not in requests
        assert manifest['news']['search'] not in requests
        eager=total-(folder/manifest['news']['catalog']).stat().st_size+(folder/manifest['news']['index']).stat().st_size
        results['public_initial'] = {'json_bytes': total, 'equivalent_eager_json_bytes':eager, 'files': json_files, 'full_graph_requests': 0}
        # Date options cover the whole corpus even before titles/URLs load.
        index=json.loads((folder/manifest['news']['index']).read_text())
        assert set(await page.locator('#day option').evaluate_all('(items)=>items.map(o=>o.value).filter(Boolean)'))=={r[2] for r in index if r[2]}
        older=index[-1]
        await page.locator('#day').select_option(older[2])
        await expect(page.locator('#result-count')).to_contain_text(f'{sum(r[2]==older[2] for r in index):,}개')
        assert manifest['news']['index'] in requests
        await expect(page.locator('#content .card h2').first).to_be_visible()
        requests.clear()
        await page.goto('http://public.test/AI-News/knowledge.html?mode=2d')
        await page.locator('#mode2d').click()
        await expect(page.locator('#nodes g').first).to_be_visible(timeout=60000)
        assert 'knowledge.json' not in requests
        results['public_graph'] = {'files': sorted(set(requests))}

        await browser.close()
        browser = await p.chromium.launch()
        page = await browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        live = os.environ.get('LIVE_SITE_URL', 'http://127.0.0.1:8001')
        for path, mode in [('/', 'reading'), ('/operations', 'operations')]:
            await page.set_viewport_size({'width': 1440, 'height': 1000})
            await page.goto(live + path)
            await expect(page.locator('html')).to_have_attribute('data-workspace', mode)
            current = 'strategy' if mode == 'reading' else 'operations'
            await expect(page.locator(f'.ws-subnav a[data-destination="{current}"]')).to_have_attribute('aria-current', 'page')
            expected = 'section[data-reading]' if mode == 'reading' else 'section[data-operations]'
            excluded = 'section[data-operations]' if mode == 'reading' else 'section[data-reading]'
            assert await page.locator(expected).first.is_visible()
            assert not await page.locator(excluded).first.is_visible()
            await page.keyboard.press('Tab')
            await expect(page.get_by_role('link', name='본문으로 이동')).to_be_focused()
            await page.keyboard.press('Enter')
            for width in (1440, 390):
                await page.set_viewport_size({'width': width, 'height': 844})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), path
            results[path] = {'mode': mode, 'skip_link': True, 'desktop_mobile': True}
        assert not errors, errors
        results['errors'] = errors
        (ROOT / '.runtime/verification/refactoring-ui.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps(results, ensure_ascii=False))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
