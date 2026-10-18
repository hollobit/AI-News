"""Deterministic entry-point dependency, global menu and error/empty contracts."""
import asyncio,os,json
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect
async def main():
 errors=[];failed=False;panel_failures=0;panel_requests=0
 async with async_playwright() as p:
  b=await p.chromium.launch();page=await b.new_page()
  page.on('pageerror',lambda e:errors.append(str(e)))
  async def route(r):
   nonlocal panel_requests
   path=urlparse(r.request.url).path
   if path=='/strategy-improvement.js':
    panel_requests+=1
    if panel_requests<=panel_failures:
     await r.fulfill(status=503,body='temporarily unavailable');return
   if path.startswith('/api/'):
    assert r.request.method=='GET'
    if failed:await r.fulfill(status=503,json={'error':'일시 조회 실패'});return
    data={'enabled':True,'runs':[]} if path=='/api/research' else {'runtime':{'ready':False},'runs':[]} if path=='/api/simulation' else {'status':'missing'}
    await r.fulfill(json=data);return
   file=ROOT/'static'/('strategy.html' if path=='/operations' else path.strip('/')+('.html' if '.' not in path else ''))
   await r.fulfill(path=str(file))
  await page.route('http://test.local/**',route)
  for path in ('research','simulation','article'):
   await page.goto('http://test.local/'+path+('?url=https://example.org/news' if path=='article' else ''))
   await expect(page.locator('.ws-global-menu a')).to_have_count(8)
   if path=='article':await expect(page.locator('#status')).to_contain_text('아직 생성한 해설')
   else:await expect(page.locator('#runs')).to_contain_text('아직')
   assert 'is not defined' not in await page.locator('body').inner_text()
   await page.set_viewport_size({'width':320,'height':844})
   await page.locator('.ws-menu-toggle').click();await expect(page.locator('.ws-global-menu a[href="/news"]')).to_be_visible()
   await page.keyboard.press('Escape');await expect(page.locator('.ws-menu-toggle')).to_be_focused()
   await page.set_viewport_size({'width':1280,'height':900})
  failed=True
  for path in ('research','simulation'):
   await page.goto('http://test.local/'+path)
   await expect(page.locator('#runs')).to_contain_text('실행 목록 조회 실패')
   await expect(page.locator('#runs')).not_to_contain_text('아직')
  # A failed script download must recover before panel initialization, and a
  # permanent failure must stop with a bounded, actionable state (no POST).
  panel_failures=1
  await page.goto('http://test.local/operations')
  await page.wait_for_function('typeof window.StrategyPanels?.improvement === "function"')
  await expect(page.locator('#workbench-load-state')).to_have_count(0)
  assert panel_requests==2
  panel_failures=100;panel_requests=0
  await page.goto('http://test.local/operations')
  await expect(page.locator('#workbench-load-state')).to_contain_text('화면 일부를 불러오지 못했습니다')
  await expect(page.get_by_role('button',name='화면 다시 불러오기')).to_be_visible()
  assert panel_requests==3
  await page.set_viewport_size({'width':320,'height':844})
  assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  assert not errors,errors
  await b.close();print(json.dumps({'entry_points':3,'global_menu':True,'mobile_escape':True,'error_not_empty':True,'errors':errors}))
asyncio.run(main())
