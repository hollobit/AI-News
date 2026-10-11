"""Question explanations, repair progress and incomplete reviews without model calls."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse, urlencode, parse_qs

ROOT=Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright, expect


async def main():
    question='벤치마크 방법과 데이터 차이는?'
    result=dict(answer='벤치마크는 평가 조건에 따라 비교 범위가 달라집니다.\n\n제공 자료의 차이가 해석에 미치는 의미를 설명합니다. <img src=x onerror=alert(1)>',
        verification=dict(method='independent_evidence_review',quality=dict(status='passed',revision_count=1,issues=[])),
        claims=[dict(text='평가 조건은 비교 범위를 제한합니다.',kind='connection',axis='비교 조건',evidence_ids=['e'])],
        evidence=[dict(id='e',title='공식 평가 자료',text='벤치마크 원문',source_url='https://example.org/source')],limitations=[])
    state=dict(status='complete',result=result)
    errors=[];posts=[];polls=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch();page=await browser.new_page(viewport={'width':1440,'height':1000})
        await page.clock.install()
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def route(r):
            path=urlparse(r.request.url).path
            if r.request.method=='POST':posts.append(path)
            if path=='/api/context':await r.fulfill(json=dict(status='ready',revision='v1',labels={},groups={}))
            elif path.startswith('/api/graph/answers/'):
                polls.append(path);await r.fulfill(json=state)
            else:
                file=ROOT/'static'/('context.html' if path=='/context' else path.lstrip('/'))
                if file.is_file():await r.fulfill(path=str(file))
                else:await r.fulfill(status=404,body='missing')
        await page.route('http://test.local/**',route)
        await page.goto('http://test.local/context?'+urlencode(dict(q=question,answer='b'*32)))
        await expect(page.locator('.answer-quality')).to_have_text('보완 후 재검토 완료')
        await expect(page.locator('.answer-synthesis > p')).to_have_count(2)
        await expect(page.locator('.answer-synthesis > p').first.locator('mark')).to_have_text('벤치마크')
        assert await page.locator('.answer-synthesis img').count()==0
        link=urlparse(await page.locator('.answer-section a').get_attribute('href'))
        assert link.path=='/context' and link.fragment=='answer-source-1'
        assert parse_qs(link.query)==dict(q=[question],answer=['b'*32])
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        result['verification']['quality'].update(status='evidence_limited',revision_count=0)
        await page.reload()
        await expect(page.locator('.answer-quality')).to_contain_text('확보한 근거 범위의 답변')
        state.update(status='needs_review',error='설명이 부족한 쟁점을 확인해 주세요.')
        result['verification']['quality'].update(status='needs_review',revision_count=1,issues=['데이터 종류가 평가에 미치는 의미의 설명이 누락되었습니다.'])
        result['answer']='질문에 충분히 답하는 종합 설명을 완성하지 못했습니다.'
        await page.reload()
        await expect(page.locator('#answer-retry')).to_be_visible()
        await expect(page.locator('.answer-quality')).to_contain_text('설명 충실도 검토 미완료')
        await expect(page.locator('.answer-quality')).to_contain_text('데이터 종류')
        await expect(page.locator('.answer-section')).to_contain_text(['확인된 자료','자료 사이','핵심 의미'])
        count=len(polls);await page.clock.run_for(15000)
        assert len(polls)==count and not posts
        state.update(status='running',result=dict(evidence=result['evidence']),progress=dict(
            percent=60,completed=3,total=5,elapsed_seconds=90,remaining_seconds=None,estimate_samples=0,
            stages=[dict(id='review',label='부족한 설명 보완',status='running',seconds=20)]))
        await page.reload()
        await expect(page.locator('#answer-status')).to_contain_text('부족한 설명 보완 진행 중')
        await expect(page.locator('#answer-meter')).to_have_attribute('value','60')
        await expect(page.locator('.answer-quality')).to_have_count(0)
        assert not errors and not posts,(errors,posts)
        print(json.dumps(dict(paragraphs=True,repair_status=True,evidence_gaps=True,incomplete_terminal=True,
            mobile=[390,320],errors=errors,model_posts=len(posts)),ensure_ascii=False))
        await browser.close()


asyncio.run(main())
