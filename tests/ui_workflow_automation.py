"""Automatic following and motion from changing snapshots; no model execution."""
import asyncio
import copy
import json
import os
from pathlib import Path
from urllib.parse import urlparse,parse_qs

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.runtime/browsers'))
from playwright.async_api import async_playwright,expect


def job(identity,status='running'):
    active=status=='running'
    steps=[dict(stage='collection',status='complete',at=10),dict(stage='strategy_draft',status='running' if active else 'complete',at=11),dict(stage='risk_assessment',status='running' if active else 'complete',at=12)]
    progress=dict(steps=steps,active=[s for s in steps if s['status']=='running'],completed=1 if active else 7,total=7,percent=14 if active else 100,revision=3)
    return dict(id=identity,key='workflow:'+identity,kind='workflow',title='자동 분석 '+identity,status=status,recorded_status=status,created_at=10,updated_at=12,href='/operations',progress=progress)


async def main():
    a=job('a','complete');b=job('b');c=job('c')
    stages=[dict(id=k,title=k,counts={'complete':2,'pending':1},total=3,complete=2,percent=66.6,status='running' if k=='deep' else 'recorded',owner_alive=k=='deep',href='/operations',unit='기사',note='선택 기간의 원장 기준',run_id='run',updated_at='2026-10-15T07:00:00Z') for k in ['collector','extract','baseline','deep','sources','papers','wiki','publication']]
    stages.extend([dict(id='index',title='검색 반영',counts={},total=0,complete=0,percent=None,status='preparing',owner_alive=True,active_count=1,href='/context',unit='검색 자료',note='현재 검토 자료를 준비합니다.'),
                   dict(id='questions',title='종합 질의',counts={'running':1},total=1,complete=0,percent=0,status='running',owner_alive=True,active_count=1,href='/context',unit='질의',note='작성과 독립 검토')])
    next(s for s in stages if s['id']=='papers')['recovery']=dict(retry_ready=12,retry_waiting=2,retry_exhausted=1)
    flow=dict(observed_at='2026-10-15T07:01:00Z',stages=stages,tasks=[],events=[],calls=[],call_counts={'running':0,'queued':0},call_limit=20,runtime_error=False,schedule={'enabled':1},scope='원장 기준',control=dict(jobs=[a],metrics={},settings={},unavailable=False))
    jobs={'a':a,'b':b,'c':c};fail_detail=False;fail_flow=False;posts=[];errors=[];hits=0
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
        async def handle(r):
            nonlocal hits
            u=urlparse(r.request.url)
            if u.path=='/api/operations/flow':
                hits+=1;await r.fulfill(status=503,json={}) if fail_flow else await r.fulfill(json=flow)
            elif u.path=='/api/operations/control':
                j=jobs[parse_qs(u.query)['id'][0]]
                events=[dict(s,id=str(i)) for i,s in enumerate(j['progress']['steps'])]
                data=dict(job=j,events=events,decisions=[],sources=[dict(id='s',title='실제 저장 근거',url='https://example.org/?id=3&part=a%26b',kind='news')],related=[],relations=[],calls=[],history_note='저장 단계 기록')
                await r.fulfill(status=503,json={}) if fail_detail else await r.fulfill(json=data)
            else:
                f=ROOT/'static'/('workflow-live.html' if u.path=='/workflow' else u.path.lstrip('/'))
                await r.fulfill(path=str(f)) if f.is_file() else await r.fulfill(status=404,body='missing')
        await page.route('http://test.local/**',handle)
        await page.goto('http://test.local/workflow')
        await expect(page.locator('#control-title')).to_have_text(a['title'])
        await expect(page.locator('.auto-stage')).to_have_count(10)
        await expect(page.locator('[data-auto-stage="papers"]')).to_contain_text('복구 대상 12 · 재시도 대기 2 · 한도 도달 1')
        assert await page.locator('.auto-panel').evaluate('(e)=>e.getBoundingClientRect().top') < await page.locator('.control-layout').evaluate('(e)=>e.getBoundingClientRect().top')
        assert await page.locator('.auto-panel').evaluate('(e)=>e.getBoundingClientRect().width') > 1200
        await expect(page.locator('[data-auto-stage="index"]')).to_have_attribute('data-activity','running')
        stages[-2].update(status='complete',owner_alive=False,active_count=0,mode='publication_delta',changed=4,removed=1,observed=120)
        await page.locator('#refresh').click()
        await page.locator('[data-auto-stage="index"]').click()
        await expect(page.locator('#auto-stage-note')).to_contain_text('변경 4개 · 철회 1개')
        await expect(page.locator('#control-follow')).to_have_attribute('aria-pressed','true')
        flow['control']['jobs']=[b,a]
        flow['calls']=[dict(id=i,model='gpt-6.1-sol',role='risk_assessment',status='running' if i==0 else 'queued',owner_alive=True,elapsed_seconds=5,started_at=10,queued_at=9) for i in range(20)]
        flow['calls'][0].update(role='strategic_question_verification',service_tier_requested='fast')
        flow['call_counts']={'running':1,'queued':19}
        await page.locator('#refresh').click();await expect(page.locator('#control-title')).to_have_text(b['title'])
        await expect(page.locator('#control-graph')).to_contain_text('2개 단계 병행')
        await expect(page.locator('.auto-call')).to_have_count(20)
        await expect(page.locator('.auto-call[data-call="0"]')).to_contain_text('종합 답변 독립 검토 · Fast 요청')
        flow['calls'][0]['service_tier_requested']='standard'
        await page.locator('#refresh').click()
        await expect(page.locator('.auto-call[data-call="0"]')).not_to_contain_text('Fast 요청')
        flow['calls'][0]['service_tier_requested']='fast'
        await page.locator('#refresh').click()
        await expect(page.locator('.auto-call[data-call="0"]')).to_contain_text('Fast 요청')
        await expect(page.locator('#control-progress')).to_contain_text('1 / 7')
        await expect(page.locator('#auto-summary')).to_contain_text('진행 작업 1개')
        assert await page.locator('.control-stream').count()==1
        await page.locator('.auto-stage[data-auto-stage="deep"]').click();await expect(page.locator('#auto-stage-note')).to_contain_text('선택 기간')
        flow['control']['jobs']=[c,b,a]
        b['progress']['steps'][1]['status']='complete';b['progress'].update(active=[b['progress']['steps'][2]],completed=2,percent=28,revision=4)
        await page.locator('#refresh').click();await expect(page.locator('#control-progress')).to_contain_text('2 / 7')
        await expect(page.locator('#control-title')).to_have_text(b['title'])
        await expect(page.locator('#control-graph')).to_contain_text('위험 분석')
        fail_detail=True
        await page.locator('#refresh').click();await expect(page.locator('#control-message')).to_contain_text('연결이 지연')
        await expect(page.locator('#control-room')).to_have_attribute('data-motion','off')
        fail_detail=False
        await page.locator('#refresh').click();await expect(page.locator('#control-room')).to_have_attribute('data-motion','on')
        # Manual inspection pins the job; automatic following can be resumed.
        await page.locator('[data-job="workflow:a"]').click();await expect(page.locator('#control-follow')).to_have_attribute('aria-pressed','false')
        await page.locator('#refresh').click();await expect(page.locator('#control-title')).to_have_text(a['title'])
        await page.locator('#control-follow').click();await expect(page.locator('#control-title')).to_have_text(c['title'])
        c.update(status='complete');c['progress']=copy.deepcopy(a['progress'])
        await page.locator('#refresh').click();await expect(page.locator('#control-title')).to_have_text(b['title'])
        await page.locator('#control-events button').first.click()
        await expect(page.locator('#control-follow')).to_have_attribute('aria-pressed','false')
        await expect(page.locator('#control-room')).to_have_attribute('data-motion','off')
        await page.locator('#control-live').click()
        await page.locator('#pause').click()
        await expect(page.locator('#control-room')).to_have_attribute('data-pipeline-motion','off')
        before=await page.locator('[data-elapsed="0"]').inner_text();count=hits
        await page.wait_for_timeout(1200)
        assert before==await page.locator('[data-elapsed="0"]').inner_text() and count==hits
        await page.locator('#pause').click()
        fail_flow=True;await page.locator('#refresh').click();await expect(page.locator('#notice')).to_be_visible()
        await expect(page.locator('#control-room')).to_have_attribute('data-pipeline-motion','off')
        fail_flow=False;await page.locator('#refresh').click();await expect(page.locator('#notice')).to_be_hidden()
        await expect(page.locator('#control-room')).to_have_attribute('data-pipeline-motion','on')
        await page.emulate_media(reduced_motion='reduce')
        assert await page.locator('.control-stream').evaluate('(e)=>getComputedStyle(e).animationName')=='none'
        out=ROOT/'.runtime/verification';out.mkdir(parents=True,exist_ok=True)
        await page.evaluate('scrollTo(0,0)');await page.screenshot(path=str(out/'automatic-flow-desktop.png'),full_page=True)
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        await page.evaluate('scrollTo(0,0)');await page.screenshot(path=str(out/'automatic-flow-mobile.png'),full_page=True)
        b.update(status='owner_missing');b['progress'].update(active=[])
        for s in b['progress']['steps']:
            if s['status']=='running':s['status']='owner_missing'
        flow['calls']=[dict(flow['calls'][0],owner_alive=False,status='owner_missing')];flow['call_counts']={'running':0,'queued':0}
        flow['stages'][3].update(status='owner_missing',owner_alive=False)
        await page.locator('#refresh').click();await expect(page.locator('.control-stream')).to_have_count(0)
        await expect(page.locator('.auto-call')).to_have_count(0)
        await expect(page.locator('.auto-stage[data-auto-stage="deep"]')).to_have_attribute('data-activity','attention')
        await page.reload();await expect(page.locator('#control-follow')).to_have_attribute('aria-pressed','false')
        assert not errors and not posts,(errors,posts)
        print(json.dumps(dict(auto_follow=True,parallel=True,manual_pin=True,replay=True,recovery=True,models=20,orphan=True,mobile=[390,320],errors=errors,analysis_posts=posts)))
        await browser.close()

asyncio.run(main())
