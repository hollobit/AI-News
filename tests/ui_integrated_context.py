"""Deterministic interaction checks; real data smoke is recorded separately."""
import asyncio,json,os
from pathlib import Path
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(Path(__file__).resolve().parents[1]/'.runtime/browsers')
from playwright.async_api import async_playwright,expect

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1000});errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  calls=[]
  labels={'news':'뉴스·원자료','strategy':'전략 주제·해석','risk':'위험','scenario':'조건부 시나리오','paper':'논문','knowledge':'지식'}
  def data():
   return {'status':'ready','revision':'v1','labels':labels,'groups':{k:[dict(id=k,title='의료 AI '+k,summary='근거와 조건을 비교합니다. <script>bad()</script>',reason='같은 원문 발췌 인용',kind=k,href='/graph',sources=[dict(title='원문',url='javascript:alert(1)',day='2026-10-11',kind='workflow',verification='accepted_analysis',excerpt='독립 검토된 해석의 원문')])] for k in labels},'scope':'검토된 해석과 원자료 관측을 구분','limitations':['인과관계 확정 아님']}
  async def api(route):
   calls.append(route.request.url)
   await route.fulfill(json=data())
  await page.route('**/api/context?*',api)
  await page.goto('http://127.0.0.1:8001/context?q=의료')
  await expect(page.locator('.lane')).to_have_count(6)
  await page.locator('.lane').first.get_by_role('button',name='비교에 담기').click()
  await expect(page.locator('#comparison')).to_be_visible()
  await page.locator('.lane').nth(4).get_by_role('button',name='이 항목 중심으로').click()
  await expect(page.locator('#back')).to_be_visible()
  await expect(page.locator('#comparison .card')).to_have_count(1)
  assert 'node=paper' in calls[-1]
  await page.locator('.lane').nth(4).get_by_role('button',name='비교에 담기').click()
  await expect(page.locator('#comparison .card')).to_have_count(2)
  assert await page.locator('a[href^="javascript:"]').count()==0
  async with page.expect_download() as info:await page.locator('#export').click()
  assert (await info.value).suggested_filename=='analysis-comparison.md'
  for width in (390,320):
   await page.set_viewport_size({'width':width,'height':844})
   assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  async def forecast(route):
   if route.request.method=='POST':await route.fulfill(json={'run':{'id':'test-forecast'}});return
   job={'id':'test-forecast','title':'의료 AI 전망','status':'complete','result':{'verified':True,'as_of':'2026-10-11','horizon_days':180,'basis':'주관적 미보정 추정','evidence':[],'report':{'summary':'조건부 전망','limitations':['통계 신뢰구간 아님'],'comparisons':[{'title':'조건부 사건','assessment':'예측이며 관측 사실 아님','method':'subjective','probability_percent':50,'probability_lower':25,'probability_upper':75,'window_start':'2026-11-01','window_end':'2027-01-01','probability_basis':'가정 기반','resolution_criterion':'공식 발표','assumptions':['성립 조건'],'counter_evidence':['반대 근거'],'update_signals':['새 발표'],'evidence_ids':[]}]}}}
   await route.fulfill(json={'item':job})
  await page.route('**/api/intelligence**',forecast)
  await page.locator('#query').fill('의료 AI')
  await page.locator('#forecast-start').click()
  await expect(page.locator('#forecast-results')).to_contain_text('50% · 주관적 추정')
  await expect(page.locator('#forecast-results')).to_contain_text('25~75% · 폭 50%p')
  assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  asked=[]
  async def ask(route):
   asked.append(route.request.post_data_json)
   await route.fulfill(json={'phase':'evidence','evidence':[],'job':{'url':'/api/graph/answers/context-test'}})
  async def answer(route):
   await route.fulfill(json={'id':'context-test','status':'complete','result':{'answer':'방법과 데이터의 의존 관계를 종합했습니다.','verification':{'method':'independent_evidence_review'},'analysis_plan':{'axes':['벤치마크 방법','데이터세트','위험']},'claims':[{'text':'평가 조건에 따라 비교 범위가 제한됩니다.','kind':'connection','axis':'벤치마크와 데이터 관계','premises':'제공 원문의 실험 조건','assumptions':'동일 환경 비교','counterevidence':'미확보','uncertainty':'실세계 일반화 미확인','evidence_ids':['e']}],'evidence':[{'id':'e','title':'논문 근거','text':'초록 내용','source_kind':'paper','source_url':'https://example.org/paper'}],'limitations':['제한된 검색 근거']}})
  await page.route('**/api/graph/ask?workspace=1',ask)
  await page.route('**/api/graph/answers/context-test',answer)
  await page.locator('#context-example').click()
  await page.locator('#context-ask-button').click()
  await expect(page.locator('#answer-status')).to_contain_text('독립 근거 검토 완료')
  await expect(page.locator('#context-answer')).to_contain_text('자료 사이의 관계')
  await expect(page.locator('#context-answer')).to_contain_text('실세계 일반화 미확인')
  assert 'Physical AI' in asked[0]['question']
  assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
  await page.screenshot(path='.runtime/verification/integrated-context-mobile.png',full_page=True)
  assert not errors,errors
  print(json.dumps({'six_domains':True,'focus':True,'comparison_preserved':True,'download':True,'mobile':[390,320],'errors':errors}))
  await browser.close()
asyncio.run(main())
