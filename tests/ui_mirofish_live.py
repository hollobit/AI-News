"""Explicit integration check: creates one draft, never starts a simulation."""
import asyncio,json,os
from pathlib import Path
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(__file__).resolve().parents[1]/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:8001/simulation?q=AI')
        await page.wait_for_load_state('networkidle')
        await expect(page.locator('#sim-q')).to_have_value('AI',timeout=60000)
        await page.locator('#sim-title').fill('Telegram 최신 AI 뉴스 · 실행 연결 검증')
        await page.locator('#sim-requirement').fill('선택 뉴스의 AI 정책 변화에 따른 이해관계자 반응을 탐색한다. 실제 예측이 아닌 조건부 시뮬레이션이며 원문 근거와 가정을 구분한다.')
        await page.locator('#sim-rounds').fill('2')
        invalid=await page.locator('#simulation-form').evaluate("f=>[...f.elements].filter(e=>e.willValidate&&!e.validity.valid).map(e=>({name:e.name,error:e.validationMessage}))")
        assert not invalid,invalid
        try:
            async with page.expect_response(lambda r:r.url.endswith('/api/simulation') and r.request.method=='POST',timeout=90000) as pending:
                await page.locator('#simulation-form button[type=submit]').click()
                assert not errors,errors
        except Exception:
            print(json.dumps({'errors':errors,'screen':(await page.locator('#content').inner_text())[:1200]},ensure_ascii=False),flush=True)
            raise
        response=await pending.value;value=await response.json()
        assert response.ok,value.get('error')
        run=value['run'];assert run['status']=='draft'
        await expect(page.get_by_role('button',name='시뮬레이션 실행',exact=True)).to_be_visible()
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors,errors
        print(json.dumps({'draft':run['id'],'status':run['status'],'runtime':await page.locator('#runtime').inner_text(),'mobile':True,'errors':errors},ensure_ascii=False))
        await browser.close()
asyncio.run(main())
