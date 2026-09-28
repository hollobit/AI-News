"""Read-only deployed Agent-Reach source management smoke test."""
import asyncio,json,os
from pathlib import Path
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(__file__).resolve().parents[1]/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
async def main():
    errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:8001/sources')
        await expect(page.locator('#pipeline-counts')).to_contain_text('전체 본문',timeout=30000)
        await expect(page.locator('#subscriptions')).to_contain_text('NVIDIA 공식 블로그')
        await expect(page.locator('#subscriptions')).to_contain_text('Google AI 공식 블로그')
        await page.locator('#passage-url').fill('https://github.com/Panniantong/Agent-Reach')
        await page.locator('#passage-query').fill('YouTube')
        await page.locator('#passage-form button').click()
        await expect(page.locator('#passages article').first).to_be_visible(timeout=30000)
        await expect(page.locator('#passages')).to_contain_text('본문 문자')
        await page.locator('#source-health button').first.click()
        await expect(page.locator('#source-detail')).to_contain_text('보관 버전과 읽기 기록')
        await page.screenshot(path='.runtime/verification/reach-upgrade-ui.png',full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors,errors
        print(json.dumps({'subscriptions':2,'passages':True,'history':True,'mobile':True,'errors':errors}),flush=True)
        await browser.close()
asyncio.run(main())
