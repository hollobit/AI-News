"""Deterministic browser coverage for modular panels, error recovery and expiry."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import sys
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.runtime/browsers'))
from playwright.async_api import async_playwright, expect
from public_data import write_data
from site_templates import public_html


async def main():
    errors = []
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        corpus = dict(news=[dict(id='one',title='의료 연구',topic='연구',day='2026-09-29',url='https://example.org',analyses=[dict(kind='기본 분석',text='환자 진단')])],
            papers=[],risks=[],risk_graph={},exported_at='2026-09-29',coverage=dict(news=1,reviewed_news=1,papers=0,reviewed_papers=0))
        graph=dict(nodes=[dict(id='source:one',type='source',title='의료 연구',news_id='one',url='https://example.org')],edges=[],pages=[],coverage={},method='Reviewed')
        first=write_data(folder,corpus,graph,now=1000)
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch()
            page=await browser.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            baseline_calls=0
            async def live(route):
                nonlocal baseline_calls
                parsed=urlparse(route.request.url);path=parsed.path;query=parse_qs(parsed.query)
                if path.startswith('/api/'):
                    if path.startswith('/api/baseline'):
                        baseline_calls+=1
                        if baseline_calls in (1,2):
                            await route.fulfill(status=503,json={'error':'temporary busy'});return
                        await route.fulfill(json={'runs':[dict(id='run',status='complete',version=1,metrics={'total':1,'verified':1})]});return
                    if path.startswith('/api/workflows') or path.startswith('/api/improvement'):
                        value={'runs':[],'enabled':False}
                    elif path=='/api/collector/channels':value={'channels':[]}
                    elif path=='/api/corpus/status':value={'status':'ready','total_unique':1,'baseline_total':1,'new_since_baseline':0}
                    elif path=='/api/strategy/topics':value={'version':1,'items':[]}
                    elif path=='/api/strategy':value={'items':[],'dates':[],'topics':[],'trends':{'lenses':[],'monitoring':{'topics':[]}},'runs':[], 'sources':{'counts':{'fetched':0},'total':0,'pending':0}, 'collector':{'last_success':None}, 'impacts':[]}
                    else:value={}
                    await route.fulfill(json=value);return
                file=ROOT/'static'/('strategy.html' if path in ('/strategy','/operations') else path.lstrip('/'))
                await route.fulfill(path=str(file)) if file.exists() else await route.fulfill(status=404)
            await page.route('http://live.test/**',live)
            await page.clock.install()
            await page.goto('http://live.test/operations')
            await expect(page.locator('#baseline-summary')).to_contain_text('temporary busy')
            await page.clock.run_for(10001)
            await expect(page.locator('#baseline-summary')).to_contain_text('temporary busy')
            assert baseline_calls==2, baseline_calls
            await page.clock.run_for(20001)
            await expect(page.locator('#baseline-state')).to_have_text('기본 분석 처리 종료')
            assert baseline_calls==3
            await page.goto('http://live.test/strategy')
            await page.locator('#topic-management').click()
            await expect(page.locator('#topic-registry-count')).to_contain_text('0개')
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            await page.close()
            page=await browser.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            requests=[]
            async def public(route):
                name=urlparse(route.request.url).path.rsplit('/',1)[-1]
                requests.append(name)
                if name=='index.html':await route.fulfill(body=public_html((ROOT/'static/public.html').read_text()),content_type='text/html');return
                file=folder/name
                if not file.exists():file=ROOT/'static'/name
                await route.fulfill(path=str(file)) if file.exists() else await route.fulfill(status=404)
            await page.route('http://public.test/**',public)
            await page.goto('http://public.test/AI-News/index.html?view=news')
            await expect(page.locator('#result-count')).to_contain_text('개 표시')
            await expect(page.locator('#content .card').first).to_be_visible()
            corpus['news'][0]['analyses'][0]['text']='갱신된 진단 근거'
            latest=write_data(folder,corpus,graph,now=1000+90000)
            for name in first['files']:
                if name not in latest['files']:(folder/name).unlink(missing_ok=True)
            old_manifest_count=requests.count('site-manifest.json')
            # Fetch an unvisited old section to trigger expiry, then verify the
            # page reload preserves its URL and admits only the new generation.
            await page.evaluate('PublicData.section("papers")') if first['papers']!=latest['papers'] else None
            # Force expiry of the lazy full-text index, which changed with text.
            await page.locator('#search').fill('갱신된')
            await expect(page.locator('#content .card').first).to_be_visible(timeout=15000)
            await expect(page.locator('#search')).to_have_value('갱신된')
            assert requests.count('site-manifest.json')>old_manifest_count
            assert 'site.json' not in requests and 'knowledge.json' not in requests
            assert not errors, errors
            print(json.dumps({'modular_panels':True,'baseline_503_recovery':baseline_calls,'generation_recovery':True,'mobile':True,'errors':errors},ensure_ascii=False))
            await browser.close()


if __name__=='__main__':asyncio.run(main())
