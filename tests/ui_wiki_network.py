"""Browser validation for live graph and Pages subpath, without external writes."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect


async def main():
    live='--live' in sys.argv
    with tempfile.TemporaryDirectory() as folder:
        if not live:
            from test_knowledge_wiki import setup,Runner
            from knowledge_wiki import KnowledgeWiki
            from export_wiki_site import export_site
            path=Path(folder)/'db';setup(path)
            service=KnowledgeWiki(path,Runner(),enabled=True,start_worker=False)
            service.compile('medical')
            site=Path(folder)/'site';export_site(path,site)
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            page=await browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            if not live:
                async def route(request):
                    u=urlparse(request.request.url)
                    if u.path=='/api/wiki/network':
                        args={k:v[0] for k,v in parse_qs(u.query).items()}
                        args['identity']=args.pop('id','');args['query']=args.pop('q','')
                        await request.fulfill(json=service.network(**args));return
                    if u.path.startswith('/demo/'):
                        name=u.path.removeprefix('/demo/') or 'index.html';base=site
                    else:name='wiki-network.html' if u.path=='/knowledge' else u.path[1:];base=ROOT/'static'
                    if name not in ('index.html','wiki-network.html','wiki-network.js','wiki-network-3d.js','wiki-network.css','three.module.js','three.core.js','knowledge.json'):
                        await request.fulfill(status=404);return
                    await request.fulfill(path=str(base/name))
                await page.route('http://127.0.0.1:8001/**',route)
            for url in ['http://127.0.0.1:8001/knowledge']+([] if live else ['http://127.0.0.1:8001/demo/']):
                await page.goto(url)
                await expect(page.locator('#mode3d')).to_have_attribute('aria-pressed','true')
                await expect(page.locator('#atlas3d canvas')).to_be_visible(timeout=30000)
                await expect(page.locator('#nodes g').first).to_be_attached(timeout=60000)
                assert await page.evaluate('window.atlas3D.pose().dist') > 0
                before=await page.evaluate('window.atlas3D.pose().az')
                canvas=page.locator('#atlas3d canvas');box=await canvas.bounding_box()
                await page.mouse.move(box['x']+box['width']*.6,box['y']+box['height']*.45)
                await page.mouse.down();await page.mouse.move(box['x']+box['width']*.72,box['y']+box['height']*.48,steps=8);await page.mouse.up()
                rotated=await page.evaluate('window.atlas3D.pose().az')
                assert rotated!=before
                assert 'az=' in page.url
                await page.reload()
                await expect(page.locator('#atlas3d canvas')).to_be_visible(timeout=30000)
                await expect(page.locator('#nodes g').first).to_be_attached(timeout=60000)
                assert await page.evaluate('window.atlas3D.pose().az')==rotated
                await page.screenshot(path=str(ROOT/'.runtime/verification'/('network-live-3d.png' if live else 'network-static-3d.png')),full_page=True)
                await page.locator('#node-list button').first.click()
                await expect(page.locator('#detail h2')).not_to_have_text('노드를 선택하세요',timeout=60000)
                focus=await page.evaluate('window.atlas3D.pose()')
                assert any(focus[key] for key in ('tx','ty','tz'))
                await page.reload()
                await expect(page.locator('#atlas3d canvas')).to_be_visible(timeout=30000)
                await expect(page.locator('#detail h2')).not_to_have_text('노드를 선택하세요',timeout=60000)
                restored=await page.evaluate('window.atlas3D.pose()')
                assert all(restored[key]==focus[key] for key in ('tx','ty','tz'))
                await page.locator('#mode2d').click()
                await expect(page.locator('#nodes g').first).to_be_visible(timeout=30000)
                await page.locator('#node-list button').first.click()
                await expect(page.locator('#detail h2')).not_to_have_text('노드를 선택하세요',timeout=60000)
                await page.locator('#reset').click()
                if live:
                    async with page.expect_response(lambda response: '/api/wiki/network?' in response.url and 'layer=semantic' in response.url,timeout=60000):
                        await page.locator('#layer').select_option('semantic')
                else:
                    await page.locator('#layer').select_option('semantic')
                await expect(page.locator('#edges .provenance')).to_have_count(0,timeout=30000)
                await page.locator('#color').select_option('community')
                await expect(page.locator('#legend')).to_contain_text('연결 성분')
                await page.locator('#search').fill('존재하지않는용어')
                await expect(page.locator('#node-list button')).to_have_count(0,timeout=60000)
                await page.locator('#search').fill('')
                await expect(page.locator('#nodes g').first).to_be_visible(timeout=60000)
                await page.locator('#network').hover();await page.mouse.wheel(0,-250)
                assert 'scale(1)' not in await page.locator('#scene').get_attribute('transform')
                await page.locator('#fit').click()
                await page.set_viewport_size({'width':390,'height':844})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                if '/demo/' in url:
                    assert await page.locator('#live-nav').count()==0
                    await expect(page.locator('#status')).to_contain_text('읽기 전용 공개 스냅샷')
                await page.screenshot(path=str(ROOT/'.runtime/verification'/('network-live.png' if live else 'network-static.png')),full_page=True)
                await page.set_viewport_size({'width':1440,'height':1000})
            if not live:
                await page.add_init_script("""(() => {const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(kind,...args){return kind.startsWith('webgl')?null:original.call(this,kind,...args);};})();""")
                await page.goto('http://127.0.0.1:8001/demo/')
                await expect(page.locator('#mode2d')).to_have_attribute('aria-pressed','true',timeout=30000)
                await expect(page.locator('#nodes g').first).to_be_visible(timeout=30000)
                await page.route('**/wiki-network-3d.js*',lambda route:route.fulfill(status=404))
                await page.goto('http://127.0.0.1:8001/demo/')
                await expect(page.locator('#mode2d')).to_have_attribute('aria-pressed','true',timeout=30000)
                await expect(page.locator('#status')).to_contain_text('3D 지도 파일을 읽지 못해',timeout=30000)
                await expect(page.locator('#nodes g').first).to_be_visible(timeout=30000)
            assert not errors,errors
            print(json.dumps(dict(live=live,static_subpath=not live,mobile=True,search=True,layers=True,zoom=True,three_dimensional=True,errors=errors)))
            await browser.close()
        if not live:service.close()

asyncio.run(main())
