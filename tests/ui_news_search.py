"""Deterministic main news search highlight, lazy body, malformed query and XSS checks."""
import asyncio, os, json
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright, expect

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(); errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        item=dict(title='ＡＩ 의료 <img src=x onerror=alert(1)>',excerpt='안전한 내용',day='2026-10-03',
            topic='general',topic_title='일반',source_url='https://example.org/a',channel='뉴스',
            chat_id=1,message_id=1,detail_id='a'*64,has_full_text=True,
            search_matches=[dict(field='심층 분석',text='진단 품질을 개선하는 AI 기술')])
        async def route(r):
            path=urlparse(r.request.url).path
            if path=='/api/news':
                await r.fulfill(json=dict(items=[item],total=1,date='all',dates=[],channels=[],topics=[],types=[]))
            elif path=='/api/news/detail': await r.fulfill(json=dict(text='원문 AI 분석'))
            elif path.startswith('/api/'): await r.fulfill(json={})
            else:
                file=ROOT/'static'/('index.html' if path=='/' else path.lstrip('/'))
                if file.is_file(): await r.fulfill(path=str(file))
                else: await r.fulfill(status=404,body='missing')
        await page.route('http://test.local/**',route)
        await page.goto('http://test.local/?view=daily&date=all&q=AI%20AND%20%EC%A7%84%EB%8B%A8')
        await expect(page.locator('.news-item')).to_have_count(1)
        await expect(page.locator('.news-item .article-title mark')).to_have_text('ＡＩ')
        await expect(page.locator('.news-item img')).to_have_count(0)
        await expect(page.locator('.news-item mark').filter(has_text='진단')).to_have_count(1)
        await page.locator('.news-item details summary').click()
        await expect(page.locator('.news-item .original mark')).to_have_text('AI')
        await page.locator('#search').fill('AI OR')
        await expect(page.locator('#search')).to_have_attribute('aria-invalid','true')
        await expect(page.locator('#results')).to_contain_text('키워드를 입력')
        # Direct highlighter verifies grapheme mapping, overlapping terms and literal metacharacters.
        result=await page.evaluate('''() => {
          const n=document.createElement('div');n.textContent='가 ＡＩ C++ <script>alert(1)</script>';
          ObservatorySearch.highlight(n, '가 OR AI OR C++');
          return {text:n.textContent, marks:[...n.querySelectorAll('mark')].map(n=>n.textContent),scripts:n.querySelectorAll('script').length};
        }''')
        assert result['marks']==['가','ＡＩ','C++'] and result['scripts']==0,result
        for width in [390,320]:
            await page.set_viewport_size(dict(width=width,height=850))
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        print(json.dumps({'highlight':True,'lazy_body':True,'unicode':True,'literal_and_xss':True,'invalid_query':True,'mobile':True,'errors':errors}))
        await browser.close()
asyncio.run(main())
