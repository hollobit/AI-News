"""An unprepared graph polls into ready without showing an empty graph as final."""
import asyncio,json,os
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch();page=await browser.new_page();errors=[];calls=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  async def route(r):
   path=urlparse(r.request.url).path
   if path=='/api/graph/integrated':
    calls.append(1)
    if len(calls)==1:await r.fulfill(status=202,json=dict(status='preparing',nodes=[],edges=[],evidence=[]))
    else:await r.fulfill(json=dict(nodes=[dict(id='n',name='준비된 근거',type='Concept',evidence_ids=[])],edges=[],evidence=[],coverage={},totals={'nodes':1}))
   elif path.startswith('/api/'):
    await r.fulfill(json=dict(input={},analysis={'status':'not_analyzed','enabled':False}))
   else:
    file=ROOT/'static'/('graph.html' if path=='/graph' else path.lstrip('/'))
    if file.is_file():await r.fulfill(path=str(file))
    else:await r.fulfill(status=404,body='missing')
  await page.route('http://test.local/**',route)
  await page.goto('http://test.local/graph',wait_until='domcontentloaded')
  await expect(page.locator('#state')).to_contain_text('준비하고 있습니다')
  await expect(page.locator('#graph')).to_contain_text('준비된 근거',timeout=10000)
  assert len(calls)==2 and not errors,errors
  print(json.dumps(dict(preparing_to_ready=True,errors=errors)));await browser.close()
asyncio.run(main())
