"""Real renderer: late search response must not replace current data on redraw."""
import asyncio,json,os,sys
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect

async def main():
    errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def route(r):
            name=urlparse(r.request.url).path.lstrip('/')
            if name=='knowledge.html':
                html=(ROOT/'static/wiki-network.html').read_text().replace('data-mode="live"','data-mode="static" data-split="true"')
                await r.fulfill(body=html,content_type='text/html');return
            if name=='public-data.js':
                await r.fulfill(content_type='application/javascript',body='''
                  window.pendingGraph={};
                  window.graphResult=title=>({nodes:[{id:'node',type:'source',title}],edges:[],pages:[],
                    detail:{id:'node',type:'source',title,connections:[]},selectedId:'node',shown:1,total:1,
                    coverage:{pages:0,cited_sources:1},method:'fixture',exported_at:'now',issues:[]});
                  window.PublicData={graphView:({q})=>q?new Promise(resolve=>pendingGraph[q]=resolve):Promise.resolve(graphResult('Initial'))};
                ''');return
            if name=='wiki-network-3d.js':await r.fulfill(body='',content_type='application/javascript');return
            file=ROOT/'static'/name
            await r.fulfill(path=str(file)) if file.is_file() else await r.fulfill(status=404)
        await page.route('http://graph.test/**',route)
        await page.goto('http://graph.test/knowledge.html#view=2d')
        await expect(page.locator('#detail h2')).to_have_text('Initial')
        await page.locator('#search').fill('older')
        await page.wait_for_function('Boolean(window.pendingGraph.older)')
        await page.locator('#search').fill('newer')
        await page.wait_for_function('Boolean(window.pendingGraph.newer)')
        await page.evaluate("pendingGraph.newer(graphResult('Latest'))")
        await expect(page.locator('#detail h2')).to_have_text('Latest')
        await page.evaluate("pendingGraph.older(graphResult('Stale'))")
        await page.locator('#color').select_option('community')
        await expect(page.locator('#detail h2')).to_have_text('Latest')
        await expect(page.locator('#nodes')).to_contain_text('Latest')
        assert 'Stale' not in await page.locator('#nodes').text_content()
        assert not errors,errors
        print(json.dumps({'out_of_order_search':True,'redraw_keeps_latest':True,'errors':errors}))
        await browser.close()

if __name__=='__main__':asyncio.run(main())
