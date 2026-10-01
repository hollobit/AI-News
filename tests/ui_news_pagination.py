"""Local news renders before slow briefing; pagination and detail fetch stay scoped."""
import asyncio,json,os
from pathlib import Path
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page();errors=[];details=[];pages=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        gate=asyncio.Event()
        async def route(r):
            u=urlparse(r.request.url);q=parse_qs(u.query);path=u.path
            if path=='/api/briefing':
                await gate.wait();await r.fulfill(json={'lead':None,'highlights':[],'topics':[],'stats':{}})
            elif path=='/api/news/detail':
                details.append(q);await r.fulfill(json={'text':'전체 수집 본문 내용'})
            elif path=='/api/news':
                n=int(q.get('page',['1'])[0]);pages.append(n)
                items=[dict(detail_id=str(i).zfill(64),title='기사 '+str(i),day='2026-10-01',topic='models',topic_title='모델',published_at='2026-10-01T09:00:00',excerpt='짧은 설명',has_full_text=True,source_url='https://example.org/'+str(i)) for i in range((n-1)*40,min(n*40,45))]
                await r.fulfill(json=dict(date='2026-10-01',dates=[dict(date='2026-10-01',count=45)],items=items,total=45,page=n,page_size=40,topics=[],types=[],channels=[]))
            elif path.startswith('/api/'):
                await r.fulfill(json={})
            else:
                file=ROOT/'static'/('index.html' if path=='/' else path.lstrip('/'))
                if file.is_file():await r.fulfill(path=str(file))
                else:await r.fulfill(status=404,body='not found')
        await page.route('http://test.local/**',route)
        await page.goto('http://test.local/?view=daily',wait_until='domcontentloaded')
        await expect(page.locator('.news-item')).to_have_count(40,timeout=10000)
        assert not gate.is_set() and not details
        await page.locator('.news-item details summary').first.click()
        await expect(page.locator('.news-item details .original').first).to_have_text('전체 수집 본문 내용')
        assert len(details)==1 and len(details[0]['detail_id'][0])==64
        await page.get_by_role('button',name='다음 40건 더 보기').click()
        await expect(page.locator('.news-item')).to_have_count(45)
        await expect(page.locator('#daily-archive-note')).to_contain_text('전체 45건 중 45건')
        assert pages==[1,2] and not errors,errors
        gate.set();await browser.close()
        print(json.dumps(dict(news_before_briefing=True,pagination=True,lazy_body=True,errors=errors)))
asyncio.run(main())
