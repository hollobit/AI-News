"""Synthetic interaction regression; --live checks real published wiki without writes."""
import asyncio,json,os,sys,tempfile
from pathlib import Path
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect

async def main():
    live='--live' in sys.argv
    with tempfile.TemporaryDirectory() as directory:
        if not live:
            from test_knowledge_wiki import setup,Runner
            from knowledge_wiki import KnowledgeWiki
            path=Path(directory)/'test.db';setup(path)
            wiki=KnowledgeWiki(path,Runner(),enabled=True,start_worker=False)
            wiki.compile('medical')
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            page=await browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            if not live:
                async def route(request):
                    u=urlparse(request.request.url)
                    if u.path=='/api/wiki':
                        if request.request.method=='POST':
                            payload=request.request.post_data_json
                            if payload.get('action')=='configure':value=wiki.manage(payload)
                            elif payload.get('action')=='alias':value=wiki.alias(payload)
                            else:value=wiki.request(payload.get('topic'))
                        else:
                            value=wiki.get(parse_qs(u.query).get('id',[None])[0])
                            if value.get('page'):
                                value['page']['claims'][0]['text']+=' <img src=x onerror="window.wikiInjected=1">'
                                value['page']['evidence'][0]['source_url']='javascript:window.wikiInjected=1'
                        await request.fulfill(json=value)
                    else:
                        name='wiki.html' if u.path=='/wiki' else u.path[1:]
                        if name not in ('wiki.html','wiki.js','wiki.css'):
                            await request.fulfill(status=404,body='');return
                        await request.fulfill(path=str(ROOT/'static'/name))
                await page.route('http://127.0.0.1:8001/**',route)
            await page.goto('http://127.0.0.1:8001/wiki?id=medical')
            if live:await expect(page.locator('#topics .topic').nth(2)).to_be_visible(timeout=30000)
            else:await expect(page.locator('#topics .topic')).to_have_count(3,timeout=30000)
            await expect(page.locator('#detail h2')).to_contain_text('의료',timeout=30000)
            await expect(page.locator('#detail .claim').first).to_be_visible(timeout=30000)
            if not live:
                assert await page.locator('#detail img').count()==0
                assert await page.evaluate('window.wikiInjected===undefined')
                assert await page.locator('#detail a[href^="javascript:"]').count()==0
            await page.locator('#detail .source summary').first.click()
            await expect(page.locator('#detail .source details[open]')).to_have_count(1)
            child=page.locator('#pages a').filter(has_text='사건').first
            if await child.count():
                await child.click();await expect(page.locator('#pages a[aria-current=page]')).to_have_count(1)
            await page.locator('#search').fill('존재하지않는검색어')
            await expect(page.locator('#pages')).to_contain_text('표시할 페이지가 없습니다')
            await page.locator('#search').fill('')
            if not live:
                await page.locator('[data-edit="medical"]').click()
                await expect(page.locator('#topic-form input[name="topic"]')).to_have_value('medical')
                await page.locator('#topic-form input[name="topic"]').fill('clinical')
                await page.locator('#topic-form input[name="name"]').fill('임상 연구')
                await page.locator('#topic-form input[name="terms"]').fill('의료 AI, 임상')
                await page.locator('#topic-form input[name="historical"]').check()
                await page.locator('#topic-form button').click()
                await expect(page.locator('#topics .topic')).to_have_count(4)
                assert next(t for t in wiki.get()['topics'] if t['id']=='clinical')['config']['historical']
                await page.locator('[data-edit="clinical"]').click()
                await page.locator('#topic-form input[name="enabled"]').uncheck()
                await page.locator('#topic-form button').click()
                await expect(page.locator('#topics')).to_contain_text('비활성화')
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            if not live:
                with wiki.db() as db:db.execute("UPDATE articles SET text='원문 변경'")
                await page.goto('http://127.0.0.1:8001/wiki?id=medical')
                await expect(page.locator('#detail')).to_contain_text('원근거가 변경')
                assert await page.locator('#detail .claim').count()==0
            assert not errors,errors
            await page.screenshot(path=str(ROOT/'.runtime/verification'/('wiki-live.png' if live else 'wiki-fixture.png')),full_page=True)
            print(json.dumps({'live':live,'mobile':True,'citations':True,'navigation':True,'errors':errors}))
            await browser.close()
        if not live:wiki.close()

asyncio.run(main())
