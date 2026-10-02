"""Real page: live updates, failed polling recovery, pause, keyboard and mobile."""
import asyncio,json,os
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1100},reduced_motion='reduce');errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  hits=0;fail=False
  def data():
   stages=[]
   for i,key in enumerate(['collector','extract','baseline','deep','sources','papers','wiki','publication']):
    done=min(2+hits,9)
    stages.append(dict(id=key,title=['Telegram 확인','기사 추출','기본 검토','상세·위험 검토','외부 원문','논문','위키','게시'][i],counts={'complete':done,'pending':10-done},total=10,complete=done,percent=done*10,status='running',owner_alive=True,unit='기사',note='각 단계 원장 기준',href='/operations',run_id='fixed',updated_at=datetime.now(timezone.utc).isoformat()))
   return dict(observed_at=datetime.now(timezone.utc).isoformat(),stages=stages,calls=[],call_limit=6,runtime_error=False,events=[],schedule={'recoveries':20,'stagnant':0},scope='단계별 기준')
  async def route(r):
   nonlocal hits
   path=__import__('urllib.parse',fromlist=['urlparse']).urlparse(r.request.url).path
   if path=='/api/operations/flow':
    if fail:await r.fulfill(status=503,json={'error':'잠시 대기'});return
    hits+=1;await r.fulfill(json=data());return
   f=ROOT/'static'/('workflow-live.html' if path=='/workflow' else path.lstrip('/'))
   if f.is_file():await r.fulfill(path=str(f))
   else:await r.fulfill(status=404,body='not found')
  await page.route('http://test.local/**',route);await page.goto('http://test.local/workflow')
  await expect(page.locator('.stage')).to_have_count(8);await expect(page.locator('#done')).to_have_text('3')
  await expect(page.locator('#done')).to_have_text('4',timeout=6000)
  await page.locator('#pause').click();prior=hits;await page.wait_for_timeout(3300);assert hits==prior
  fail=True;await page.locator('#refresh').click();await expect(page.locator('#notice')).to_be_visible();await expect(page.locator('#done')).to_have_text('4')
  fail=False;await page.locator('#refresh').click();await expect(page.locator('#notice')).to_be_hidden();await expect(page.locator('#done')).to_have_text('5')
  await page.locator('[data-stage="baseline"]').focus();await page.keyboard.press('Enter');await expect(page.locator('#detail-title')).to_have_text('기본 검토')
  await page.locator('#filter-attention').click();assert await page.locator('.stage.dim').count()==8
  await page.locator('#filter-all').click();await page.locator('[data-stage="deep"]').click()
  await expect(page.locator('.ws-global-menu a')).to_have_count(15)
  out=ROOT/'.runtime/verification';out.mkdir(parents=True,exist_ok=True)
  await page.screenshot(path=str(out/'workflow-live-desktop.png'),full_page=True)
  for width in [390,320]:
   await page.set_viewport_size({'width':width,'height':844});assert not await page.evaluate('document.documentElement.scrollWidth>innerWidth')
  await page.screenshot(path=str(out/'workflow-live-mobile.png'),full_page=True)
  assert not errors,errors
  print(json.dumps({'live_updates':True,'error_recovery':True,'pause':True,'keyboard':True,'mobile_widths':[390,320],'errors':errors}));await browser.close()
asyncio.run(main())
