import asyncio,json,os
from pathlib import Path
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(__file__).resolve().parents[1]/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        data=json.loads(Path('.runtime/verification/article-live.json').read_text())
        from urllib.parse import quote
        await page.goto('http://127.0.0.1:8001/article?url='+quote(data['url'],safe=''))
        await expect(page.locator('h1')).to_have_text('기사 상세 해설')
        await expect(page.locator('#status')).not_to_be_empty()
        state=await page.request.get('http://127.0.0.1:8001/api/article-explanations?url='+quote(data['url'],safe=''))
        result=await state.json()
        if result['status']=='complete':
            await expect(page.locator('#result')).to_contain_text('이 뉴스가 중요한 이유')
            sections=result['result']['sections']
            for key,label in [('uncertainty','판단에 중요한 조건'),('signals','예정된 후속 변화')]:
                if not sections.get(key):assert await page.get_by_role('heading',name=label,exact=True).count()==0
            assert not await page.locator('.observation-details').get_attribute('open')
            await page.locator('.observation-details > summary').click()
            await expect(page.locator('svg')).to_be_visible()
            await page.locator('#result details').first.locator('summary').click()
            await expect(page.locator('blockquote').first).to_be_visible()
        await page.screenshot(path='.runtime/verification/article-explanation.png',full_page=True)
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors,errors
        Path('.runtime/verification/article-live-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({'status':result['status'],'error':result.get('error'),'mobile':True,'errors':errors},ensure_ascii=False))
        await browser.close()
asyncio.run(main())
