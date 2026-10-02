"""Shared navigation, initial readiness and URL state; no mutating requests."""
import asyncio,json,os
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect

async def main():
 errors=[];result={}
 async with async_playwright() as p:
  browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1000})
  page.on('pageerror',lambda e:errors.append(str(e)))
  paths=['/workflow','/strategy','/operations','/news','/archive','/observatory','/intelligence','/risks','/papers','/research','/simulation','/sources','/wiki','/knowledge','/graph','/article']
  paths += ['/intelligence/'+v for v in ('events','topics','decisions','scenarios','research','operations','concepts','experiments','profiles','risk_history')]
  for path in paths:
   print('Checking',path,flush=True)
   await page.goto('http://127.0.0.1:8001'+path,wait_until='domcontentloaded')
   await expect(page.locator('.ws-global-menu a')).to_have_count(15)
   links=await page.locator('.ws-global-menu a').evaluate_all('(links)=>links.map(a=>a.getAttribute("href"))')
   assert len(set(links))==15
   if path.startswith('/intelligence'):await expect(page.locator('#sections a')).to_have_count(11)
   if path=='/news':await expect(page.locator('nav.date-nav')).to_be_visible()
   if path=='/knowledge':await expect(page.get_by_role('link',name='Obsidian ZIP')).to_be_visible()
   expected=0 if path=='/article' else 1
   await expect(page.locator('.ws-global-menu [aria-current=page]')).to_have_count(expected)
   for width in (390,320):
    await page.set_viewport_size({'width':width,'height':844});await page.wait_for_timeout(500)
    await expect(page.locator('.ws-menu-toggle')).to_have_attribute('aria-expanded','false')
    await page.locator('.ws-menu-toggle').click()
    await expect(page.locator('.ws-global-menu a[href="/news"]')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.ws-menu-toggle')).to_be_focused()
    await expect(page.locator('.ws-menu-toggle')).to_have_attribute('aria-expanded','false')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(path,width,await page.evaluate('document.documentElement.scrollWidth'))
   await page.set_viewport_size({'width':1440,'height':1000})
   assert 'is not defined' not in await page.locator('body').inner_text(),path
  for path,selector,sort,option in [('/papers','#paper-search','#paper-window','28'),('/risks','#risk-search','#risk-sort','recent'),('/strategy','#search','#sort-filter','latest')]:
   await page.goto('http://127.0.0.1:8001'+path)
   await page.locator(selector).fill('의료');await page.wait_for_timeout(600)
   await expect(page).to_have_url(__import__('re').compile('q='))
   await page.locator(sort).select_option(option);await page.wait_for_timeout(150)
   await page.reload();await expect(page.locator(selector)).to_have_value('의료');await expect(page.locator(sort)).to_have_value(option)
   await page.go_back();await expect(page.locator(selector)).to_have_value('의료');assert await page.locator(sort).input_value()!=option
   result[path]='search reload/back restored'
  await page.goto('http://127.0.0.1:8001/wiki');await page.locator('.ws-global-menu a[href="/news"]').click();await expect(page).to_have_url('http://127.0.0.1:8001/news')
  assert not errors,errors
  print(json.dumps({'pages':len(paths),'widths':[1440,390,320],'route_state':result,'errors':errors}))
  await browser.close()
asyncio.run(main())
