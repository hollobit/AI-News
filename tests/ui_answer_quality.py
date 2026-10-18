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
    result=dict(answer='벤치마크는 평가 조건에 따라 비교 범위가 달라집니다. [e]\n\n제공 자료의 차이가 해석에 미치는 의미를 설명합니다. [unknown:source] <img src=x onerror=alert(1)>',
        verification=dict(method='independent_evidence_review',quality=dict(status='passed',revision_count=1,issues=[])),
        claims=[dict(text='평가 조건은 비교 범위를 제한합니다.',kind='connection',axis='비교 조건',evidence_ids=['e'])],
        evidence=[dict(id='e',title='공식 평가 자료',text='벤치마크 원문',source_url='https://example.org/source'),
                  dict(id='extra',title='공식 평가 조건',text='현장 적용은 미검증이다.',source_url='https://example.org/source')],
        retrieval=dict(candidate_documents=120,unique_documents=1,cited_documents=1,passages=2,
                       budget=dict(documents=32,passages=44),budget_limited=True,
                       candidate_limit_per_query=60,reserved_documents=2,
                       axes=[dict(axis='데이터세트',candidate_documents=0,selected_documents=0,status='no_candidates')]),
        limitations=[],workspace_as_of=1792306489,source_scope_note='선택한 자료의 현재 입력·인용·독립 검토를 다시 확인합니다.')
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
        await expect(page.locator('#context-answer')).to_contain_text('자료 준비')
        await expect(page.locator('#context-answer')).to_contain_text('현재 입력·인용·독립 검토')
        await expect(page.locator('.evidence-counts dd')).to_have_text(['120개 문서','1개 문서','2개','1개 발췌'])
        await expect(page.locator('#context-answer')).not_to_contain_text('인용 원자료')
        await page.locator('.evidence-summary summary').click()
        await expect(page.locator('.evidence-summary')).to_contain_text('검색 후보 없음')
        await expect(page.locator('.evidence-summary')).to_contain_text('일부 후보를 제외')
        await expect(page.locator('.evidence-summary')).to_contain_text('최대 60개 후보 문서')
        await expect(page.locator('.evidence-summary')).to_contain_text('2개 문서를 우선 배정')
        await expect(page.locator('.answer-synthesis > p')).to_have_count(2)
        await expect(page.locator('.answer-synthesis > p').first.locator('mark')).to_have_text('벤치마크')
        assert await page.locator('.answer-synthesis img').count()==0
        await expect(page.locator('.answer-citations a')).to_have_text('[1]')
        await expect(page.locator('.answer-synthesis')).to_contain_text('[unknown:source]')
        await page.locator('.answer-citations a').click()
        await expect(page.locator('#answer-source-1')).to_be_visible()
        link=urlparse(await page.locator('.answer-section a').get_attribute('href'))
        assert link.path=='/context' and link.fragment=='answer-source-1'
        assert parse_qs(link.query)==dict(q=[question],answer=['b'*32])
        assert not await page.locator('.answer-support').evaluate('(el)=>el.open')
        await page.locator('.answer-support > summary').click()
        await expect(page.locator('.answer-section .card')).to_be_visible()
        await page.locator('.answer-section a').click()
        await expect(page.locator('#answer-source-1')).to_be_visible()
        assert urlparse(page.url).path=='/context' and not posts
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
        assert await page.locator('.answer-support').evaluate('(el)=>el.open')
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
        await expect(page.locator('.evidence-counts dd')).to_have_text(['기록 없음','기록 없음','2개','검토 전'])
        state['progress'].update(completed=5,total=5,percent=100,stages=[dict(id='finalize',label='최신 근거 확인·완료',status='complete',seconds=1)])
        await page.reload()
        await expect(page.locator('#answer-status')).to_contain_text('검토 완료 · 결과와 최신 근거 확인 중')
        state['progress'].update(planning=dict(method='jev_template',reason='template_selected'),
            planning_note='질문 유형·계획 적합성을 확인했습니다. 답변 작성과 독립 검토는 이어서 진행합니다.',
            stages=[dict(id='plan',label='Jev 검색 계획 선택',status='complete',seconds=.2),
                    dict(id='synthesis',label='관계 분석·답변 작성',status='running',seconds=2)])
        await page.reload()
        await expect(page.locator('[data-stage="plan"]')).to_contain_text('Jev 검색 계획 선택')
        await expect(page.locator('[data-stage="plan"] small')).to_contain_text('독립 검토는 이어서 진행')
        for width in (390,320):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        state['progress'].update(planning=dict(method='sol',reason='unavailable'),
            planning_note='Jev 연결이 지연되어 Sol이 검색 계획을 작성합니다.',
            stages=[dict(id='plan',label='Sol 질문 해석·검색 계획',status='running',seconds=2)])
        await page.reload()
        await expect(page.locator('#answer-progress')).to_contain_text('Jev 연결이 지연')
        await expect(page.locator('#answer-status')).to_contain_text('Sol 질문 해석·검색 계획 진행 중')
        assert not errors and not posts,(errors,posts)
        print(json.dumps(dict(paragraphs=True,repair_status=True,evidence_gaps=True,incomplete_terminal=True,
            mobile=[390,320],errors=errors,model_posts=len(posts)),ensure_ascii=False))
        await browser.close()


asyncio.run(main())
