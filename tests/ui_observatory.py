"""Read-only synthetic browser regression, plus optional --live server check."""
import asyncio,json,os,sys,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect
from observatory import build_projection
from keyword_index import document_id,keyword_record_id
from datetime import date,timedelta

async def verify(live=False):
 errors=[];failed=False;revision=1;hits=0
 days=[(date(2026,9,3)+timedelta(days=i)).isoformat() for i in range(14)]
 items=[dict(chat_id=1,message_id=i,item_index=0,day=days[d],title=f'실제 근거 {i}',text='AI 안전장치와 수출통제',source_url=f'https://example.org/{i}') for i,d in enumerate([0,7,7,8,13])]
 morph={keyword_record_id(i):[dict(id=k,label=label,kind='noun_phrase') for k,label in [('a','안전장치'),('b','수출통제')]] for i in items}
 topics=[dict(id='signal-group:safeguards',label='AI 안전장치',grouped=True)]
 data=build_projection(items,morph,topics,{topics[0]['id']:{(document_id(i),i['day']) for i in items}},days)
 data['nodes'].append(dict(id='unsupported-topic',label='근거 없는 주제',kind='topic',count=7,series=[1]*14,current=7,previous=7,evidence_by_day=[['missing-evidence'] for _ in days]))
 async def route(r):
  nonlocal hits
  path=r.request.url.split('9999')[-1].split('?')[0]
  if r.request.method!='GET':raise AssertionError('No mutations expected')
  if path in ['/observatory','/observatory.js','/observatory-search.js','/observatory.css']:
   name=path[1:]+('.html' if path=='/observatory' else '')
   await r.fulfill(body=(ROOT/'static'/name).read_bytes(),content_type='text/html' if name.endswith('html') else 'text/css' if name.endswith('css') else 'application/javascript');return
  if path=='/api/observatory':
   hits+=1
   await r.fulfill(status=503 if failed else 200,json={'error':'temporary'} if failed else dict(data,version=str(revision),computed_at='2026-09-16T10:00:00Z'));return
  fixture={
   '/api/collector/channels':{'last_success':'2026-09-16T10:00:00Z','pipeline':{'status':'complete','extracted_at':'2026-09-16T10:00:01Z'},'channels':[{'name':'hollobit_news','last_checked_at':'2026-09-16T10:00:00Z'}]},
   '/api/corpus/status':{'total_unique':5},'/api/sources':{'counts':{'fetched':3},'pending':0,'total':5},
   '/api/baseline':{'runs':[{'status':'complete','metrics':{'analyzed':5,'total':5}}]},
   '/api/improvement':{'runs':[{'status':'paused','metrics':{'verified_unique':2,'total_unique':5}}]}}
  await r.fulfill(json=fixture.get(path,{}))
 async with async_playwright() as p:
  browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='no-preference')
  page.on('pageerror',lambda e:errors.append(str(e)))
  if not live:await page.route('**/*',route)
  await page.goto('http://127.0.0.1:'+('8001' if live else '9999')+'/observatory',wait_until='domcontentloaded')
  await expect(page.locator('#network .node').first).to_be_visible(timeout=180000)
  if live:days=(await (await page.request.get('http://127.0.0.1:8001/api/observatory')).json())['days']
  await expect(page.locator('#refresh')).to_be_enabled(timeout=60000)
  await expect(page.locator('#motion')).to_have_attribute('aria-pressed','true')
  await page.emulate_media(reduced_motion='reduce')
  await expect(page.locator('#motion')).to_have_attribute('aria-pressed','false')
  await expect(page.locator('#stages .stage')).to_have_count(6)
  await expect(page.locator('#channel-time')).to_contain_text('hollobit_news')
  await page.locator('#topics button').first.click()
  await expect(page.locator('#evidence article').first).to_be_visible()
  assert await page.locator('#network .edge').evaluate_all("es=>es.some(e=>Number(e.style.opacity)>.3)")
  await expect(page.locator('#network .node[aria-pressed=true]')).to_have_count(1)
  if not live:
   await expect(page.locator('#topics')).not_to_contain_text('근거 없는 주제')
   await expect(page.locator('#focus option[value="unsupported-topic"]')).to_have_count(0)
   await expect(page.locator('#network .node[data-id="unsupported-topic"]')).to_be_hidden()
   await page.locator('#day').fill('2')
   await expect(page.locator('#topics button')).to_have_count(0)
   await expect(page.locator('#network .node:visible')).to_have_count(0)
   await expect(page.locator('#network .edge:visible')).to_have_count(0)
   await expect(page.locator('#heatmap tbody tr')).to_have_count(0)
   await expect(page.locator('#focus option')).to_have_count(1)
   await expect(page.locator('#selection-title')).to_have_text('근거 살펴보기')
   await page.locator('#all').click()
   await expect(page.locator('#topics button')).to_have_count(1)
   await expect(page.locator('#network .node:visible')).to_have_count(3)
  cell=page.locator('#heatmap tbody tr').first.locator('button:not(:disabled)').first if live else page.locator('#heatmap tbody tr').first.locator('button').nth(7)
  chosen_day=int(await cell.get_attribute('data-day'))
  await cell.click()
  await expect(page.locator('#day-label')).to_have_text(days[chosen_day])
  await expect(page.locator('#all')).to_have_attribute('aria-pressed','false')
  if not live:
   await expect(page.locator('#selection-meta')).to_contain_text('고유 문서 2개')
   await expect(page.locator('#evidence article')).to_have_count(2)
   edge=page.locator('#network .edge').first;await edge.focus();await page.keyboard.press('Enter')
   await expect(page.locator('#selection-title')).to_contain_text('↔')
   await page.locator('#play').click();await expect(page.locator('#day-label')).to_have_text(days[8],timeout=3000)
   await page.locator('#play').click()
   await page.locator('#focus').select_option('keyword:a')
   revision=2
   await page.locator('#refresh').click();await expect(page.locator('#refresh')).to_be_enabled()
   await expect(page.locator('#focus')).to_have_value('keyword:a')
   await expect(page.locator('#selection-title')).to_have_text('안전장치')
   failed=True;await page.locator('#refresh').click();await expect(page.locator('#error')).to_be_visible()
   await expect(page.locator('#network .node:visible')).to_have_count(3)
   failed=False;await expect(page.locator('#refresh')).to_be_enabled();await page.locator('#refresh').click()
   await expect(page.locator('#error')).to_be_hidden()
  if not live:
   await page.locator('#all').click()
   await page.locator('#theme-search').fill('수출통제')
   await expect(page.locator('#theme-count')).to_contain_text('검색 결과')
   await expect(page.locator('#topics button')).to_have_count(1)
   await expect(page.locator('#network .node.search-match')).to_have_count(1)
   await expect(page.locator('#network .node[data-id="keyword:b"]')).to_be_visible()
   await page.locator('#theme-search').fill('안전장치 수출통제')
   await expect(page.locator('#topics button')).to_have_count(1)
   await expect(page.locator('#network .node:visible')).to_have_count(3)
   await page.locator('#theme-search').fill('없는단어 OR 수출통제')
   await expect(page.locator('#topics button')).to_have_count(1)
   await page.locator('#theme-search').fill('"AI 안전장치"')
   await expect(page.locator('#topics button')).to_have_count(1)
   await page.locator('#theme-search').fill('안전장치 -수출통제')
   await expect(page.locator('#network .node:visible')).to_have_count(0)
   await page.locator('#theme-search').fill('안전장치 OR')
   await expect(page.locator('#search-error')).to_be_visible()
   await expect(page.locator('#theme-search')).to_have_attribute('aria-invalid','true')
   await page.locator('#theme-search').fill('없는검색결과')
   await expect(page.locator('#network .node:visible')).to_have_count(0)
   await expect(page.locator('#topics button')).to_have_count(0)
   await page.locator('#search-clear').click()
   await expect(page.locator('#network .node:visible')).to_have_count(3)
   await page.locator('#theme-filter').select_option('falling')
   await expect(page.locator('#network .node:visible')).to_have_count(0)
   await page.locator('#theme-filter').select_option('all')
   await page.locator('#focus').select_option('keyword:a')
   pos=await page.locator('#network .node[data-id="keyword:a"]').get_attribute('transform')
   import re
   x,y=map(float,re.findall(r'-?\d+(?:\.\d+)?',pos))
   assert abs(x-350)<45 and abs(y-250)<45,(x,y)
   await page.locator('#network-motion').click()
   await expect(page.locator('#network .flowing')).to_have_count(0)
   await page.locator('#network-motion').click()
   await page.locator('#log-scale').check()
  await page.locator('#all').click();await page.locator('#reset').click()
  await expect(page.locator('#selection-title')).to_have_text('근거 살펴보기')
  if live:
   label=await page.locator('#focus option').nth(1).inner_text()
   label=label.split(' · ',1)[1]
   await page.locator('#theme-search').fill('"'+label+'" OR __no_match__')
   await expect(page.locator('#theme-count')).to_contain_text('검색 결과')
   assert await page.locator('#network .node:visible').count()>0
   await page.locator('#theme-search').fill('"'+label+'" -"'+label+'"')
   await expect(page.locator('#network .node:visible')).to_have_count(0)
   await page.locator('#search-clear').click()
   await expect(page.locator('#topics button').first).to_be_visible()
  await page.set_viewport_size({'width':390,'height':844})
  assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  await page.locator('#topics button').first.click();await expect(page.locator('#evidence article').first).to_be_visible()
  assert not errors,errors
  await page.set_viewport_size({'width':1440,'height':1100})
  await page.screenshot(path=str(ROOT/'.runtime/verification'/('observatory-live.png' if live else 'observatory-fixture.png')),full_page=True)
  print(json.dumps({'live':live,'nodes':await page.locator('#network .node').count(),'edges':await page.locator('#network .edge').count(),'poll_requests':hits,'mobile':True,'errors':errors}))
  await browser.close()
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');asyncio.run(verify(parser.parse_args().live))
