"""The architecture document must work without the conversation host or network."""
import asyncio
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.runtime/browsers'))
from playwright.async_api import async_playwright, expect

async def main():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto((ROOT / 'docs/news-architecture.html').as_uri())
        for theme in ('light', 'dark'):
            await page.emulate_media(color_scheme=theme)
            for width in (1440, 820, 540, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 1000})
                for view in ('architecture', 'collection', 'publication'):
                    button = page.locator(f'[data-view="{view}"]')
                    await button.click()
                    await expect(button).to_have_attribute('aria-pressed', 'true')
                    await page.wait_for_timeout(80)
                    failures = await page.evaluate('''() => {
                      const failures = [];
                      if(document.documentElement.scrollWidth > innerWidth) failures.push('overflow');
                      for(const group of document.querySelectorAll('svg g')) {
                        const r=group.querySelector('rect').getBBox();
                        if(getComputedStyle(group.querySelector('rect')).fill==='rgb(0, 0, 0)') failures.push('missing fill');
                        for(const text of group.querySelectorAll('text')) {
                          const b=text.getBBox();
                          if(b.x<r.x || b.x+b.width>r.x+r.width || b.y<r.y || b.y+b.height>r.y+r.height) failures.push(text.textContent);
                        }
                      }
                      return failures;
                    }''')
                    assert not failures, (theme, width, view, failures)
        await page.set_viewport_size({'width': 1080, 'height': 1280})
        await page.emulate_media(color_scheme='light')
        await page.locator('[data-view="architecture"]').click()
        output=ROOT/'.runtime/verification/architecture-fixed.png'
        output.parent.mkdir(parents=True,exist_ok=True)
        await page.screenshot(path=str(output),full_page=True)
        assert not errors, errors
        await browser.close()
        print('Standalone diagram: 3 views x 5 widths x 2 themes passed; JS errors 0')

asyncio.run(main())
