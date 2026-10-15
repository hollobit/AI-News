"""Control room using deterministic API events: no model calls or analysis writes."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse,parse_qs

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect


def fixture():
    start=1792031000
    jobs=[dict(id=k,key='workflow:'+k,kind='workflow',title=t,status='complete',recorded_status='complete',created_at=start,updated_at=start+70,href='/operations#workflow') for k,t in [('a','Physical AI · 로봇 벤치마크와 안전 검토'),('b','의료 AI · 임상 검증 근거 비교')]]
    decision=dict(id=1,subject_kind='workflow',subject_id='a',model='jev-1.14.0',status='complete',cached=False,elapsed_ms=223.22,started_at=start+4,finished_at=start+5,input_hash='abc123',context={'historical_comparison':False},questions={'risk':dict(instructions='안전 검토가 필요한가?',criteria={'required':'안전성 검증이 필요하다','routine':'일반 안내','unknown':'불충분'})},baseline={'path':'parallel-drafts-v1'},result={'answers':{'risk':dict(type='choice',choice='required',confidence=.9,probabilities={'required':.93,'routine':.04,'unknown':.03})},'usage':{'input_tokens':736,'output_tokens':112}})
    jobs[0]['decision']=dict(id=1,status='complete')
    events=[dict(id=str(i),stage=s,status=v,at=start+i*10) for i,(s,v) in enumerate([('collection','complete'),('enrichment','complete'),('strategy_draft','running'),('strategy_draft','complete'),('integrated_verification','running'),('integrated_verification','complete'),('final','complete')])]
    events[1:1]=[dict(id='j-start',stage='jev',status='running',at=start+4),dict(id='j-end',stage='jev',status='complete',at=start+5)]
    data=dict(job=jobs[0],events=events,decisions=[decision],sources=[dict(id='s'+str(i),title=t,url='https://example.org/article?id='+str(i)+'&section=ai',kind='stored_news_excerpt') for i,t in enumerate(['로봇 벤치마크 실험 결과','안전성 평가 방법','현장 도입의 제약','공식 논문 초록','성능 비교와 데이터세트'])],route='parallel-drafts-v1',calls=[],related=[dict(id='t',name='Physical AI',type='Topic'),dict(id='p',name='로봇 평가 논문',type='Paper'),dict(id='r',name='안전성 검토',type='Risk')],relations=[dict(source='t',target='p',relation='related_to')],history_note='저장 당시 입력과 기록 · 인과관계가 아님')
    stages=[dict(id=k,title=k,counts={'complete':2,'pending':1},total=3,complete=2,percent=66.6,status='recorded',unit='기사',note='원장 기준',href='/operations',run_id='cycle') for k in ['collector','extract','baseline','deep','sources','papers','wiki','publication']]
    flow=dict(observed_at='2026-10-15T02:30:00Z',stages=stages,tasks=[],calls=[],call_counts={'running':0,'queued':0},call_limit=20,runtime_error=False,events=[],schedule={},scope='단계별 원장',control=dict(jobs=jobs,decisions=[decision],metrics=dict(calls=1,cached=0,skipped=0,p50_ms=223.22,input_tokens=736),settings={'enabled':True},window=80,unavailable=False))
    return flow,data


async def main():
    flow,data=fixture();errors=[];posts=[];fail=False;hits=0
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1440,'height':1100},reduced_motion='reduce')
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
        async def route(r):
            nonlocal hits
            url=urlparse(r.request.url);path=url.path
            if path=='/api/operations/flow':
                hits+=1
                await r.fulfill(status=503,json={'error':'wait'}) if fail else await r.fulfill(json=flow)
            elif path=='/api/operations/control':
                identity=parse_qs(url.query)['id'][0]
                if identity=='b':
                    await asyncio.sleep(.25)
                    await r.fulfill(json=dict(data,job=flow['control']['jobs'][1],decisions=[],events=[]))
                else:await r.fulfill(json=data)
            else:
                f=ROOT/'static'/('workflow-live.html' if path=='/workflow' else path.lstrip('/'))
                await r.fulfill(path=str(f)) if f.is_file() else await r.fulfill(status=404,body='missing')
        await page.route('http://test.local/**',route)
        await page.goto('http://test.local/workflow')
        await expect(page.locator('#control-title')).to_have_text(data['job']['title'])
        await expect(page.locator('#control-inspector')).to_contain_text('상세 위험 검토 필요')
        await expect(page.locator('.control-job')).to_have_count(2)
        await expect(page.locator('#flow-board')).to_be_hidden()
        assert await page.locator('#control-graph .control-node').count()==8
        source=page.locator('#control-graph [role="button"]').first
        await source.focus();await page.keyboard.press('Enter')
        await expect(page.locator('#control-inspector')).to_contain_text('원출처 열기')
        assert (await page.locator('#control-inspector a').get_attribute('href')).endswith('?id=0&section=ai')
        # Switch to another job and back while its response is in flight.
        await page.locator('[data-job="workflow:b"]').click()
        await page.locator('[data-job="workflow:a"]').click()
        await expect(page.locator('#control-title')).to_have_text(data['job']['title'])
        await page.wait_for_timeout(350)
        await expect(page.locator('#control-title')).to_have_text(data['job']['title'])
        # Past cursor must hide later Jev conclusions, not merely move a slider.
        await page.locator('#control-events button').first.click()
        await expect(page.locator('#control-inspector')).to_contain_text('이 시점에는 Jev 판단 기록이 없습니다')
        assert await page.locator('#control-inspector').get_by_text('상세 위험 검토 필요',exact=True).count()==0
        await page.locator('#control-play').click()
        await expect(page.locator('#control-play')).to_have_attribute('aria-pressed','true')
        await expect(page.locator('#control-inspector')).to_contain_text('상세 위험 검토 필요',timeout=4000)
        await page.locator('#control-live').click()
        await expect(page.locator('#control-clock')).to_contain_text('최신 기록')
        await page.locator('#control-search').fill('의료');await expect(page.locator('.control-job')).to_have_count(1)
        await page.locator('#control-search').fill('');await page.locator('#control-filter').select_option('jev');await expect(page.locator('.control-job')).to_have_count(1)
        await page.locator('#control-filter').select_option('all')
        await page.locator('#pause').click();before=hits;await page.wait_for_timeout(3200);assert hits==before
        fail=True;await page.locator('#refresh').click();await expect(page.locator('#notice')).to_be_visible();await expect(page.locator('#control-title')).to_have_text(data['job']['title'])
        fail=False;await page.locator('#refresh').click();await expect(page.locator('#notice')).to_be_hidden()
        await page.locator('#view-board').click();await expect(page.locator('#flow-board')).to_be_visible()
        await page.locator('#view-control').click();await expect(page.locator('#control-room')).to_be_visible()
        out=ROOT/'.runtime/verification';out.mkdir(parents=True,exist_ok=True)
        await page.screenshot(path=str(out/'decision-control-desktop.png'),full_page=True)
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        await page.screenshot(path=str(out/'decision-control-mobile.png'),full_page=True)
        await page.reload();await expect(page.locator('#control-title')).to_have_text(data['job']['title'])
        # A dead Jev owner without a finish event is not still evaluating.
        data['decisions'][0].update(status='owner_missing',finished_at=None,result={})
        data['events']=[e for e in data['events'] if e['id']!='j-end']
        await page.reload()
        await expect(page.locator('#control-inspector')).to_contain_text('실행자 없음')
        await expect(page.locator('#control-inspector')).not_to_contain_text('평가 요청 처리 중')
        assert not errors and not posts,(errors,posts)
        print(json.dumps(dict(keyboard=True,source_query_preserved=True,selection_race=True,replay_hides_future=True,reload_selection=True,pause_recovery=True,mobile=[390,320],errors=errors,analysis_posts=posts)))
        await browser.close()

asyncio.run(main())
