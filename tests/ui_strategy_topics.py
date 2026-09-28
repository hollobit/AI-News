"""Browser regression for strategy filters; all traffic uses synthetic fixtures.

Run: PLAYWRIGHT_BROWSERS_PATH=... .venv/bin/python tests/ui_strategy_topics.py
An optional --root PATH runs the same checks against a staged static directory.
No live server, credentials, registry writes, or model calls are used.
"""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.async_api import async_playwright, expect


async def verify(root):
    errors, requests, blocked = [], [], []
    news_gate = None
    gate_lens = None
    fail_next = False
    fail_page_two = False
    overview_gate = asyncio.Event()
    lenses = [dict(id=identity, name=label, terms=[label], series=[1, 2],
                   current=2, previous=1, growth_pct=100, share_change_pp=1)
              for identity, label in [('china', '중국'), ('us', '미국'), ('dynamic:ai', '자동 AI')]]
    lenses += [dict(lenses[0], id='manual:empty',name='자료 없는 주제',current=0,previous=1,origin='manual'),dict(lenses[0],id='manual:no-comparison',name='비교 없는 주제',current=2,previous=0,origin='manual')]
    overview = dict(total=30, url_groups=30, sources={'counts': {}, 'total': 0, 'pending': 0},
                    collector={}, runs=[], trends=dict(start='2026-09-01', end='2026-09-15',
                    method='합성 근거', weighting='합성 가중치', current_documents=2,
                    previous_documents=1, lenses=lenses, emerging=[], monitoring={}))

    overview['trends']['monitoring'] = {'topics':[
        {'id':'signal-group:safeguards','name':'근거가 있는 변화','grouped':True,
         'series':[0]*7+[2]+[0]*6,'days':[f'2026-09-{i:02}' for i in range(3,17)],
         'members':[{'label':'안전장치','origin':'automatic','current':2,'previous':0,'series':[0]*7+[2]+[0]*6}],'current':2,'previous':1,'count':2,
         'observation_types':{'proposed':0,'negated_or_conditional':0,'reported_mention':2},
         'evidence':[{'title':'변화 보도','url':'https://example.org/evidence','quote':'AI 통제 정책이 변경되었다.'}]},
        {'id':'empty-signal','name':'근거 없는 변화','current':0,'count':0,'evidence':[]}], 'sources':[]}

    async def route(request_route):
        nonlocal fail_next, fail_page_two
        request = request_route.request
        url = urlsplit(request.url)
        if url.hostname=='127.0.0.1' and url.path=='/api/graph/ask' and request.method=='POST':
            await request_route.fulfill(json={'answer':'합성 검토 답변입니다.',
                'claims':[{'text':'합성 근거의 주장입니다.','evidence_ids':['fixture']}],
                'evidence':[{'id':'fixture','title':'합성 인용 원문','text':'원문 발췌'}],
                'limitations':[],'timing':{'total_ms':120,'answer_cache_hit':True}})
            return
        if url.hostname != '127.0.0.1' or request.method != 'GET':
            blocked.append(request.method + ' ' + url.path)
            await request_route.abort()
            return
        static = {'/strategy': 'strategy.html', '/strategy.js': 'strategy.js', '/strategy.css': 'strategy.css'}
        if url.path in static:
            suffix = static[url.path].rsplit('.', 1)[1]
            await request_route.fulfill(body=(root / 'static' / static[url.path]).read_bytes(),
                content_type={'html': 'text/html', 'js': 'application/javascript', 'css': 'text/css'}[suffix])
            return
        query = parse_qs(url.query)
        if url.path == '/api/strategy':
            if query.get('view') == ['overview']:
                await overview_gate.wait()
                data = overview
            else:
                requests.append(query)
                identity = query.get('lens', [''])[0]
                page_number = int(query.get('page', ['1'])[0])
                gate = news_gate
                if gate is not None and (gate_lens is None or identity == gate_lens):
                    await gate.wait()
                if fail_next or (fail_page_two and page_number == 2):
                    fail_next = fail_page_two = False
                    await request_route.fulfill(status=503, json={'error': '합성 일시 오류'})
                    return
                total = 0 if identity == 'us' else 18 if identity else 30
                start = (page_number - 1) * 12
                data = dict(total=total, unique_documents=total, page=page_number,
                    items=[dict(item_id=f'{identity}:{i}', title=f'중국 Qwen 뉴스 {i}',
                            excerpt='중국의 Qwen 발표와 <b>원문</b> 표기',matched_terms=['중국','Qwen'] if identity=='china' else [],
                            day='2026-09-15', topic='ai', strategic_keywords=[])
                           for i in range(start, min(start + 12, total))],
                    dates=[{'date': '2026-09-15', 'count': 30}], topics=[{'id': 'ai', 'title': 'AI'}])
        elif url.path == '/api/simulation':
            data = {'runtime': {}}
        elif url.path == '/api/risks':
            data = {'coverage': {}, 'risks': []}
        elif '/api/graph/' in url.path:
            data = {'nodes': [], 'edges': [], 'evidence': [], 'summary': {}}
        else:
            data = {'runs': []}
        try:
            await request_route.fulfill(json=data)
        except Exception:
            if not request.failure:
                raise

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.route('**/*', route)
        try:
            await page.goto('http://127.0.0.1:9999/strategy', wait_until='domcontentloaded')
            all_button = page.get_by_role('button', name='전체 전략 주제', exact=True)
            # The all-topics control is available while overview aggregation is pending.
            await expect(all_button).to_be_visible(timeout=3000)
            overview_gate.set()
            china = page.locator('#lens-tabs [data-lens="china"]')
            await expect(china).to_be_visible()
            await expect(page.locator('#monitoring-signals')).to_contain_text('변화 관련 언급 2건')
            await expect(page.locator('#monitoring-signals')).not_to_contain_text('제안 언급 0건')
            await expect(page.locator('#monitoring-signals')).not_to_contain_text('부정·조건부 0건')
            await expect(page.locator('#monitoring-signals')).not_to_contain_text('근거 없는 변화')
            await expect(page.locator('#monitoring-signals')).to_contain_text('직전 7일 대비 +1건 (+100%)')
            await expect(page.locator('#refresh')).to_be_enabled()
            await expect(page.locator('#news-count')).to_have_text('30')

            news_gate = asyncio.Event(); gate_lens = 'china'
            await china.evaluate("button => button.scrollIntoView({behavior:'instant',block:'center'})")
            initial_scroll = await page.evaluate('() => scrollY')
            await china.click()
            await expect(china).to_have_attribute('aria-pressed', 'true', timeout=700)
            await expect(page.locator('#news-grid')).to_have_attribute('aria-busy', 'true')
            await expect(page.locator('#scope-label')).to_contain_text('중국 · 뉴스 근거를 불러오는 중')
            await page.wait_for_timeout(300)
            assert abs(await page.evaluate('() => scrollY') - initial_scroll) < 2, 'Topic tabs must not jump the page'
            # All-topics selection must supersede an unfinished topic request.
            await all_button.click()
            await expect(all_button).to_have_attribute('aria-pressed', 'true', timeout=700)
            await expect(page.locator('#news-count')).to_have_text('30')
            assert 'lens' not in requests[-1]
            news_gate.set(); news_gate = None
            await page.wait_for_timeout(150)
            await expect(page.locator('#news-count')).to_have_text('30')
            await expect(all_button).to_have_attribute('aria-pressed', 'true')

            # Re-selecting a tab preserves its topic and keyboard activation resets it.
            await china.click(); await expect(page.locator('#news-count')).to_have_text('18')
            await expect(page.locator('[data-lens="manual:empty"]')).to_have_count(0)
            await expect(page.locator('#lens-cards [data-lens="manual:no-comparison"]')).to_have_count(1)
            await expect(page.locator('#lens-cards [data-lens="manual:no-comparison"]')).to_contain_text('신규 관측 · 이전 기간 비교 불가')
            await expect(page.locator('#news-grid .news-title mark')).to_have_count(24)
            await expect(page.locator('#news-grid p mark')).to_have_count(24)
            await expect(page.locator('#news-grid p b')).to_have_count(0)
            await china.click(); await expect(page.locator('#refresh')).to_be_enabled()
            assert requests[-1]['lens'] == ['china']
            await all_button.focus(); await page.keyboard.press('Enter')
            await expect(page.locator('#news-count')).to_have_text('30')

            await expect(page.locator('#news-grid mark')).to_have_count(0)

            # All topics clears only the lens/keyword scope, preserving other filters.
            await page.locator('#date-filter').select_option('2026-09-15')
            await expect(page.locator('#refresh')).to_be_enabled()
            await page.locator('#sector-filter').select_option('medical')
            await expect(page.locator('#refresh')).to_be_enabled()
            await page.locator('#search').fill('AI')
            await china.click(); await expect(page.locator('#news-count')).to_have_text('18')
            await all_button.click(); await expect(page.locator('#news-count')).to_have_text('30')
            assert all(requests[-1].get(k) == [v] for k, v in {'q': 'AI', 'date': '2026-09-15', 'sector': 'medical'}.items())
            assert not any(k in requests[-1] for k in ['lens', 'terms', 'keyword', 'strategic_keyword'])
            await page.locator('#clear-filters').click(); await expect(page.locator('#refresh')).to_be_enabled()

            # Empty results remain usable.
            await page.locator('#lens-tabs [data-lens="us"]').click()
            await expect(page.locator('#news-count')).to_have_text('0')
            await expect(page.locator('#news-grid')).to_contain_text('현재 조건에 맞는 뉴스가 없습니다')
            await all_button.click(); await expect(page.locator('#news-count')).to_have_text('30')

            # Failed page 2 must retry page 2 rather than skip ahead to page 3.
            fail_page_two = True
            await page.locator('#more-news').click(); await expect(page.locator('#scope-label')).to_contain_text('합성 일시 오류')
            await page.locator('#more-news').click(); await expect(page.locator('#news-grid .news-card')).to_have_count(24)
            assert requests[-1]['page'] == ['2']

            # An overview refresh cannot replace pending news with a stale selection.
            news_gate = asyncio.Event(); gate_lens = None
            await all_button.evaluate('button => { window.originalAllButton = button; }')
            overview['trends']['current_documents'] += 1
            await page.locator('#refresh').click()
            await page.wait_for_timeout(150)
            await expect(page.locator('#scope-label')).to_contain_text('뉴스 근거를 불러오는 중')
            assert await all_button.evaluate('button => button === window.originalAllButton'), 'Overview refresh must preserve topic controls'
            await expect(page.locator('#news-grid')).to_have_attribute('aria-busy', 'true')
            news_gate.set(); news_gate = None
            await expect(page.locator('#refresh')).to_be_enabled()

            fail_next = True
            await china.click()
            await expect(page.locator('#news-grid')).to_contain_text('합성 일시 오류')
            await page.get_by_role('button', name='다시 시도', exact=True).click()
            await expect(page.locator('#news-count')).to_have_text('18')

            # Simulate a stalled server without waiting 30 real seconds.
            await page.clock.install()
            news_gate = asyncio.Event(); gate_lens = None
            await all_button.click()
            await page.clock.fast_forward(30001)
            await expect(page.locator('#news-grid')).to_contain_text('뉴스 조회가 지연되고 있습니다')
            await expect(page.locator('#news-grid')).to_have_attribute('aria-busy', 'false')
            news_gate.set(); news_gate = None
            await page.get_by_role('button', name='다시 시도', exact=True).click()
            await expect(page.locator('#news-count')).to_have_text('30')

            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.locator('#lens-tabs [data-lens="dynamic:ai"]').click()
            await expect(page.locator('#news-count')).to_have_text('18')
            await all_button.click(); await expect(page.locator('#news-count')).to_have_text('30')
            assert (await all_button.bounding_box())['height'] >= 44
            assert not await page.evaluate('() => document.documentElement.scrollWidth > innerWidth')
            await page.locator('#question').fill('검토된 근거는 무엇인가?')
            await page.locator('#ask-button').click()
            await expect(page.locator('#answer')).to_contain_text('같은 질문과 근거의 저장 답변')
            await page.locator('#answer a',has_text='[근거 1]').click()
            await expect(page.locator('#strategy-answer-source-1')).to_be_focused()
            await expect(page.locator('#strategy-answer-source-1')).to_be_visible()
            await expect(page.locator('#ask-button')).to_be_enabled()
            await page.locator('#momentum-panel').evaluate("el => {el.open=true}")
            await page.locator('.signal-members > summary').click()
            await expect(page.locator('.signal-members')).to_contain_text('자동 발견 · 최근 7일 2건')
            await expect(page.locator('.signal-members')).to_contain_text('증감률 계산 불가')
            daily=page.locator('#monitoring-signals > article > .signal-trend .signal-daily')
            await daily.locator('summary').click()
            await expect(daily.locator('table')).to_contain_text('2026-09-10')
            await expect(daily.locator('tr')).to_have_count(15)
            await page.get_by_role('button',name='이 신호의 뉴스 보기',exact=True).click()
            await expect(page.locator('#scope-label')).to_contain_text('근거가 있는 변화')
            await expect(page.locator('#news-count')).to_have_text('18')
            assert requests[-1]['lens']==['signal-group:safeguards']
            await all_button.click()
            await expect(page.locator('#news-count')).to_have_text('30')
            assert not errors, errors
            assert not blocked, blocked
            print(json.dumps({'passed': True, 'checks': ['early_all_control', 'immediate_selection', 'loading_state',
                'stable_scroll', 'rapid_switch', 'same_topic', 'keyboard', 'preserved_filters', 'empty_results',
                'pagination_retry', 'overview_race', 'http_retry', 'timeout_retry', 'mobile','answer_citations','signal_groups','signal_trends','signal_news'],
                'news_requests': len(requests), 'page_errors': errors, 'external_or_write_requests': blocked}, ensure_ascii=False))
        finally:
            overview_gate.set()
            if news_gate is not None:
                news_gate.set()
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    asyncio.run(verify(parser.parse_args().root))
