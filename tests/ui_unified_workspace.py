"""Shared spaces, public/live handoff, keyboard palette and mobile boundaries."""
import asyncio, json, os
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect
import sys
sys.path.insert(0,str(ROOT))
from site_templates import public_html

async def main():
 errors=[];posts=[];results=[]
 async with async_playwright() as p:
  b=await p.chromium.launch();page=await b.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.on('request',lambda r:posts.append(r.url) if '/api/' in r.url and r.method!='GET' else None)
  async def public(route):
   name=urlparse(route.request.url).path.removeprefix('/AI-News/') or 'index.html'
   if name=='remote-access.json':await route.fulfill(json={'origin':'https://live.example','mode':'configured'});return
   alias={'index.html':'public.html','context.html':'remote-context.html'}
   if name in alias:
    html=public_html((ROOT/'static'/alias[name]).read_text())
    if name=='context.html':html=html.replace('</body>','<script src="public-navigation.js"></script></body>')
    await route.fulfill(content_type='text/html',body=html);return
   if name in ('knowledge.html','observatory.html') or name.endswith('.json'):
    file=ROOT/'.runtime/public-site'/name
   else:file=ROOT/'static'/name
   if not file.is_file():raise AssertionError('Unexpected public asset '+name)
   await route.fulfill(path=str(file))
  await page.route('http://public.test/**',public)
  public_base='http://public.test/AI-News/'
  for base,path in [('http://127.0.0.1:8001','/strategy'),('http://127.0.0.1:8001','/context?q=AI'),(public_base,'index.html?view=strategy'),(public_base,'context.html?destination=simulation&q=AI&date=2026-10-11')]:
   await page.goto(base+path,wait_until='domcontentloaded')
   await expect(page.locator('.ws-global-menu a')).to_have_count(8)
   await expect(page.locator('.ws-global-menu [aria-current=page]')).to_have_count(1)
   await expect(page.locator('.ws-menu-toggle')).to_have_attribute('aria-expanded','false')
   assert await page.locator('.ws-header').evaluate('''e=>{let brand=e.querySelector('.ws-brand-row').getBoundingClientRect(),nav=e.querySelector('.ws-global-menu').getBoundingClientRect(),tabs=e.querySelector('.ws-workspace-row').getBoundingClientRect();return nav.top>=brand.bottom-1&&tabs.top>=nav.bottom-1&&nav.width>=innerWidth-2}'''),'Header rows must span the screen without overlapping'
   if path.startswith('/context'):
    await expect(page.locator('.ws-global-menu a[data-destination="news"]')).to_have_attribute('href','/news?q=AI')
   if 'view=strategy' in path:await expect(page.locator('#notice')).to_contain_text('읽기 전용 공개 스냅샷',timeout=30000)
   if 'destination=simulation' in path:
    await expect(page.locator('#remote-open')).to_have_attribute('href','https://live.example/simulation?q=AI&date=2026-10-11')
    await page.locator('#remote-query').fill('의료 & AI')
    await expect(page.locator('#remote-open')).to_have_attribute('href',__import__('re').compile('q=%EC%9D%98%EB%A3%8C'))
   await page.locator('.ws-menu-toggle').click();await expect(page.locator('.ws-spaces-panel')).to_be_visible()
   assert await page.locator('.ws-spaces-grid .ws-space-card').count()==8
   await page.keyboard.press('Escape');await expect(page.locator('.ws-menu-toggle')).to_be_focused()
   await page.keyboard.press('Control+k');await expect(page.locator('.ws-palette')).to_be_visible()
   await page.locator('.ws-palette-input').fill('시뮬레이션');await expect(page.locator('.ws-command-option')).to_have_count(2)
   await page.keyboard.press('ArrowDown');await expect(page.locator('.ws-command-option').nth(1)).to_have_attribute('aria-selected','true')
   await page.keyboard.press('Escape');await expect(page.locator('.ws-command-trigger')).to_be_focused()
   for width in (390,320):
    await page.set_viewport_size({'width':width,'height':844});assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(base,path,width)
    await page.locator('.ws-command-trigger').click();assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    await page.keyboard.press('Escape')
   await page.set_viewport_size({'width':1440,'height':1000});results.append(path)
  await page.goto(public_base+'index.html?view=news&q=AI&day=2026-10-11')
  await expect(page.locator('#notice')).to_contain_text('읽기 전용 공개 스냅샷',timeout=30000)
  await page.locator('.ws-subnav a[data-destination="archive"]').click()
  await expect(page.locator('#remote-title')).to_have_text('URL 수정 이력')
  await expect(page.locator('#remote-open')).to_be_visible()
  u=urlparse(await page.locator('#remote-open').get_attribute('href'))
  assert u.path=='/archive' and 'date=2026-10-11' in u.query and 'q=AI' in u.query
  await page.goto(public_base+'context.html?destination=forecast&q=AI&forecast=saved123')
  await expect(page.locator('#remote-open')).to_have_attribute('href',__import__('re').compile('forecast=saved123#forecast-panel$'))
  await page.goto(public_base+'context.html?destination=https://evil.example')
  await expect(page.locator('#remote-status')).to_contain_text('확인해 주세요')
  await expect(page.locator('#remote-open')).to_be_hidden()
  await page.goto(public_base+'index.html?view=simulation&q=AI')
  await expect(page.locator('#mirofish-remote')).to_have_attribute('href',__import__('re').compile('context.html\\?destination=simulation&q=AI'))
  assert await page.locator('a[href*="127.0.0.1"]').count()==0
  for suffix,name in [('index.html?view=strategy','public'),('context.html?destination=context','analysis')]:
   await page.goto(public_base+suffix);await page.wait_for_timeout(1000)
   await page.screenshot(path=str(ROOT/'.runtime/verification'/('unified-'+name+'-desktop.png')),full_page=False)
   if name=='public':
    await page.locator('.ws-command-trigger').click();await page.screenshot(path=str(ROOT/'.runtime/verification/unified-command.png'));await page.keyboard.press('Escape')
   await page.set_viewport_size({'width':390,'height':844});await page.screenshot(path=str(ROOT/'.runtime/verification'/('unified-'+name+'-mobile.png')));await page.set_viewport_size({'width':1440,'height':1000})
  assert not errors,errors
  assert not posts,posts
  summary={'spaces':8,'tested_pages':results,'mobile':[390,320],'palette_keyboard':True,'handoff_context':True,'unsafe_destination_rejected':True,'errors':errors,'analysis_posts':len(posts)}
  (ROOT/'.runtime/verification/unified-workspace-ui.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False));await b.close()
asyncio.run(main())
