import asyncio,json,os
from pathlib import Path
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path.cwd()/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch()
  page=await browser.new_page(viewport={'width':1440,'height':1000})
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto('http://127.0.0.1:8001/operations#pipeline-health')
  await expect(page.locator('[data-pipeline-stages] article')).to_have_count(4,timeout=30000)
  await expect(page.locator('[data-pipeline-models]')).to_contain_text('gpt-6.1-sol')
  await expect(page.locator('[data-pipeline-calls]')).to_contain_text('gpt-6.1-sol',timeout=30000)
  await expect(page.locator('#pipeline-health-state')).to_contain_text('2초 자동 갱신')
  before=await page.locator('#pipeline-health-state').text_content()
  await page.wait_for_function('(before) => document.querySelector("#pipeline-health-state").textContent !== before', arg=before, timeout=12000)
  await page.locator('#pipeline-health').screenshot(path='.runtime/verification/pipeline-health-desktop.png')
  for width in [390,320]:
   await page.set_viewport_size({'width':width,'height':844})
   assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
  await page.screenshot(path='.runtime/verification/pipeline-health-mobile.png',full_page=True)
  response=await page.request.get('http://127.0.0.1:8001/api/operations/health')
  fixture=await response.json()
  fixture['incidents']=[dict(id=999999,message='검증용 지연',action='검증용 조치',opened_at=fixture['checked_at'],last_seen_at=fixture['checked_at'],resolved_at=None,acknowledged_at=None,notification_status='requested')]
  await page.route('**/api/operations/health',lambda route:route.fulfill(json=fixture))
  async def ack(route):
   fixture['incidents'][0]['acknowledged_at']=fixture['checked_at']
   await route.fulfill(json={'acknowledged':True})
  await page.route('**/api/operations/incidents/999999/ack',ack)
  await page.locator('[data-pipeline-refresh]').click()
  await expect(page.locator('[data-pipeline-incidents]')).to_contain_text('검증용 지연')
  await page.get_by_role('button',name='확인했습니다').click()
  await expect(page.locator('[data-pipeline-incidents]')).to_contain_text('확인함 · 미해소')
  assert not errors,errors
  print(json.dumps({'stages':4,'models_visible':True,'automatic_refresh':True,'mobile':[390,320],'errors':errors}))
  await browser.close()
asyncio.run(main())
