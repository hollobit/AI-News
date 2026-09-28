import asyncio,json,os
from pathlib import Path
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(__file__).resolve().parents[1]/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1400,'height':1000});errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto('http://127.0.0.1:8001/papers')
  await expect(page.locator('#paper-pipeline-state')).to_contain_text('자동 수집·분석',timeout=30000)
  response=await page.request.get('http://127.0.0.1:8001/api/papers/pipeline');state=await response.json()
  assert state['enabled'] and 'providers' in state,state
  await page.set_viewport_size({'width':390,'height':844});assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  await page.goto('http://127.0.0.1:8001/observatory')
  await page.locator('[data-paper-research]>summary').click()
  await expect(page.locator('[data-paper-results]')).to_contain_text('제목·초록 확보',timeout=30000)
  assert not errors,errors
  await page.screenshot(path='.runtime/verification/paper-pipeline-ui.png',full_page=True)
  print(json.dumps({'pipeline':state,'errors':errors,'mobile':True,'research_panel':True},ensure_ascii=False))
  await browser.close()
asyncio.run(main())
