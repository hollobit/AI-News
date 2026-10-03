"""Deterministic public dashboard search/filter regression."""
import asyncio,json,os
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect

async def main():
    news=[dict(id='one',title='의료 연구',topic='연구',day='2026-09-19',url='https://example.org/one',analyses=[dict(kind='기본 분석',text='환자 진단 연구')]),
          dict(id='two',title='반도체 공급',topic='산업',day='2026-09-18',url='https://example.org/two',analyses=[])]
    obs=dict(days=['2026-09-18','2026-09-19'],comparison_days=14,limits={},edges=[dict(source='medical',target='patient')],
        nodes=[dict(id='medical',kind='topic',label='의료',count=8,current=8,previous=3,series=[0,8],evidence_by_day=[[],['a']]),
               dict(id='chip',kind='topic',label='반도체',count=9,current=9,previous=4,series=[9,0],evidence_by_day=[['b'],[]]),
               dict(id='patient',kind='keyword',label='환자')],evidence={'a':dict(title='의료 연구',url=news[0]['url']),'b':dict(title='반도체 공급',url=news[1]['url'])})
    site=dict(news=news,papers=[],risks=[],exported_at='2026-09-19',coverage=dict(news=2,reviewed_news=1,papers=0,reviewed_papers=0))
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page();errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def route(r):
            name=urlparse(r.request.url).path.rsplit('/',1)[-1]
            if name=='site-manifest.json':await r.fulfill(status=404,body='Missing legacy manifest')
            elif name=='site.json':await r.fulfill(json=site)
            elif name=='knowledge.json':await r.fulfill(json={'pages':[],'nodes':[]})
            elif name=='observatory-14-default.json':await r.fulfill(json=obs)
            else:await r.fulfill(path=str(ROOT/'static'/('public.html' if name=='index.html' else name)))
        await page.route('http://test.local/**',route)
        await page.goto('http://test.local/AI-News/index.html?view=strategy')
        cards=page.locator('#dashboard-topics .card')
        await expect(cards).to_have_count(2)
        await page.locator('#search').fill('환자')
        await expect(cards).to_have_count(1)
        await expect(cards.first.locator('h2')).to_have_text('의료')
        await expect(page.locator('#result-count')).to_contain_text('공개 자료 1개')
        await page.reload();await expect(cards).to_have_count(1)
        await page.locator('#search').fill('의료 AND 진단 -반도체')
        await expect(page.locator('#content .card h2').filter(has_text='의료 연구')).to_have_count(1)
        await expect(page.locator('#content mark').filter(has_text='진단')).to_have_count(1)
        assert await page.locator('#content details').first.get_attribute('open') is not None
        await page.locator('#search').fill('AI OR')
        await expect(page.locator('#result-count')).to_contain_text('키워드를 입력')
        await page.locator('#search').fill('not_found_123')
        await expect(cards).to_have_count(0)
        await expect(page.locator('#content .card')).to_have_count(0)
        await expect(page.locator('#result-count')).to_contain_text('공개 자료 0개')
        await page.locator('#search').fill('')
        await expect(cards).to_have_count(2)
        await page.locator('#day').select_option('2026-09-18')
        await expect(cards.first.locator('h2')).to_have_text('반도체')
        await expect(cards).to_have_count(1)
        await page.locator('#reviewed').check()
        await expect(cards).to_have_count(0)
        await page.locator('#day').select_option('')
        await expect(cards.first.locator('h2')).to_have_text('의료')
        await page.locator('#topic').select_option('산업')
        await expect(cards).to_have_count(0)
        assert not errors,errors
        print(json.dumps({'search_all_sections':True,'linked_keyword':True,'empty_and_clear':True,'date_topic_reviewed':True,'reload':True,'errors':errors}))
        await browser.close()
asyncio.run(main())
