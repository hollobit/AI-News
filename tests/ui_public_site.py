"""Exercise the published-only files under a repository subpath."""
import asyncio,json,os,sys
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'.runtime/browsers')
from playwright.async_api import async_playwright,expect

async def main():
    folder=Path(os.environ.get('PUBLIC_SITE_DIR',str(ROOT/'.runtime/public-site')));errors=[];requests=[]
    remote='--remote' in sys.argv
    if remote:expect.set_options(timeout=30000)
    base='https://hollobit.github.io/AI-News/' if remote else 'http://public.test/AI-News/'
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
        page.on('pageerror',lambda e:errors.append(str(e)))
        async def route(r):
            u=urlparse(r.request.url);requests.append(u.path)
            name=u.path.removeprefix('/AI-News/') or 'index.html'
            file=folder/name
            if not u.path.startswith('/AI-News/') or not file.is_file():
                errors.append('unexpected request '+u.path);await r.fulfill(status=404);return
            await r.fulfill(path=str(file))
        if not remote:await page.route('http://public.test/**',route)
        else:page.on('request',lambda r:requests.append(urlparse(r.url).path))
        for view in ('strategy','news','archive','research','risks','papers','wiki','sources','simulation','services'):
            await page.goto(base+'index.html?view='+view)
            await expect(page.locator('#notice')).to_contain_text('읽기 전용 공개 스냅샷',timeout=60000)
            assert await page.locator('#menu a').count()==12
            if view=='simulation':
                await page.locator('#search').fill('AI')
                await expect(page.locator('#mirofish-local')).to_have_attribute('href','http://127.0.0.1:8001/simulation?q=AI')
                await page.reload()
                await expect(page.locator('#search')).to_have_value('AI')
            if view!='services':
                await expect(page.locator('#content .card').first).to_be_visible()
                await page.locator('#search').fill('unfindable_zzz_123')
                await expect(page.locator('#result-count')).to_contain_text('공개 자료 0개')
                if view=='strategy':
                    await expect(page.locator('#dashboard-topics .card')).to_have_count(0)
                    await expect(page.locator('#content .card')).to_have_count(0)
                    await page.reload()
                    await expect(page.locator('#search')).to_have_value('unfindable_zzz_123')
                    await expect(page.locator('#dashboard-topics .card')).to_have_count(0)
                    await page.locator('#search').fill('')
                    await expect(page.locator('#dashboard-topics .card').first).to_be_visible()
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),view
            await page.set_viewport_size({'width':1440,'height':1000})
        context=await page.evaluate("""async()=>{const s=await(await fetch('site.json')).json();const k=await(await fetch('knowledge.json')).json();let a;if(k.coverage.all_reviewed_documents_connected){const ids=new Set(k.nodes.map(n=>n.news_id).filter(Boolean));if(!s.news.filter(a=>a.analyses.length).every(a=>ids.has(a.id)))throw Error('Reviewed news missing from knowledge graph');a=s.news.find(a=>a.analyses.length);}else a=s.news.find(a=>a.analyses.length&&!k.nodes.some(n=>n.type==='source'&&n.url===a.url));return a?{...a,graph_title:k.nodes.find(n=>n.news_id===a.id)?.title||a.title}:null;}""")
        assert context,'Need a reviewed article to verify source/analysis connections'
        await page.goto(base+'index.html?view=news&id='+context['id'])
        await page.locator('#content .links a').filter(has_text='지식 연결').click()
        await expect(page.locator('#detail h2')).to_have_text(context['graph_title'],timeout=30000)
        assert await page.locator('#detail .connection').count()>0
        assert await page.locator('#edges line, #edges path').count()>0
        await page.reload()
        await expect(page.locator('#detail h2')).to_have_text(context['graph_title'],timeout=30000)
        selected_title=await page.locator('#detail .connection button').first.text_content()
        await page.locator('#detail .connection button').first.click()
        await expect(page.locator('#detail h2')).to_have_text(selected_title,timeout=30000)
        await page.reload()
        await expect(page.locator('#detail h2')).to_have_text(selected_title)
        await page.goto(base+'observatory.html')
        await expect(page.locator('#network .node').first).to_be_visible(timeout=30000)
        full=await page.evaluate("""async()=>{const d=await(await fetch('observatory-90-expanded.json')).json();const n=d.nodes.filter(n=>n.kind==='topic').sort((a,b)=>b.count-a.count)[0];return {id:n.id,count:n.count,version:d.document_index_version};}""")
        assert full['version']==1 and full['count']>50
        await page.goto(base+'observatory.html?window=90&expand=1&id='+full['id'])
        await expect(page.locator('#evidence .evidence-item')).to_have_count(50)
        for width in (1440,390):
            await page.set_viewport_size({'width':width,'height':844})
            assert await page.locator('#evidence').evaluate("e=>getComputedStyle(e).maxHeight==='none' && getComputedStyle(e).overflowY==='visible' && e.clientHeight>430 && e.scrollHeight<=e.clientHeight+1")
        await page.set_viewport_size({'width':1440,'height':1000})
        await expect(page.locator('#document-count')).to_contain_text(f"전체 {full['count']:,}개")
        await page.locator('#more-documents').click()
        await expect(page.locator('#evidence .evidence-item')).to_have_count(min(100,full['count']))
        await page.reload()
        await expect(page.locator('#evidence .evidence-item')).to_have_count(min(100,full['count']))
        async with page.expect_download() as pending:
            await page.locator('#download-documents').click()
        download=await pending.value
        payload=json.loads(Path(await download.path()).read_text())
        assert len(payload['documents'])==full['count']
        await page.locator('#document-search').fill('unfindable_zzz_123')
        await expect(page.locator('#evidence .evidence-item')).to_have_count(0)
        await page.reload()
        await expect(page.locator('#document-search')).to_have_value('unfindable_zzz_123')
        await expect(page.locator('#evidence .evidence-item')).to_have_count(0)
        await page.goto(base+'observatory.html')
        await expect(page.locator('#network .node').first).to_be_visible(timeout=30000)
        for days in ('30','90','14'):
            await page.locator('#window').select_option(days)
            await expect(page.locator('#all')).to_have_text(days+'일 전체')
        await page.locator('#expand-graph').click()
        await expect(page.locator('#scope')).to_contain_text('확장 표시 중')
        await page.locator('#topics button').first.click()
        await expect(page.locator('#evidence .evidence-item').first).to_be_visible()
        assert await page.locator('#evidence .evidence-item p').count()==0
        await page.set_viewport_size({'width':390,'height':844})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        await page.screenshot(path=str(ROOT/'.runtime/verification/public-observatory.png'),full_page=True)
        await page.goto(base+'knowledge.html')
        await page.locator('#mode2d').click()
        await expect(page.locator('#nodes g').first).to_be_visible(timeout=30000)
        # Round-trip every content route and its selected view state.
        await page.goto(base+'index.html?view=strategy')
        curve=page.get_by_role('link',name='곡선·근거·관계 보기').first
        await expect(curve).to_be_visible()
        label=await curve.locator('..').locator('h2').inner_text()
        href=await curve.get_attribute('href');assert 'id=' in href and 'window=' in href
        await curve.click()
        await expect(page.locator('#selection-title')).to_have_text(label)
        await page.reload()
        await expect(page.locator('#selection-title')).to_have_text(label)
        await page.locator('#expand-graph').click()
        await expect(page.locator('#scope')).to_contain_text('확장 표시 중')
        selected=await page.locator('#topics button').last.get_attribute('data-id')
        await page.locator('#topics button').last.click()
        title=await page.locator('#selection-title').inner_text()
        await page.reload()
        await expect(page.locator('#selection-title')).to_have_text(title)
        assert 'expand=1' in page.url
        await page.locator('#reset').click()
        await page.go_back()
        await expect(page.locator('#selection-title')).to_have_text(title)
        await page.locator('#theme-search').fill('AI')
        await page.wait_for_timeout(250)
        await page.reload()
        await expect(page.locator('#theme-search')).to_have_value('AI')
        await page.goto(base+'observatory.html?window=30&expand=1')
        await expect(page.locator('#all')).to_have_text('30일 전체')
        keyword=page.locator('#focus option').filter(has_text='키워드').first
        keyword_id=await keyword.get_attribute('value')
        await page.locator('#focus').select_option(keyword_id)
        keyword_title=await page.locator('#selection-title').inner_text()
        await page.reload()
        await expect(page.locator('#selection-title')).to_have_text(keyword_title)
        edge=page.locator('#network .edge[data-visible="true"]').first
        await edge.focus();await page.keyboard.press('Enter')
        edge_title=await page.locator('#selection-title').inner_text()
        await page.reload()
        await expect(page.locator('#selection-title')).to_have_text(edge_title)
        cell=page.locator('#heatmap button:not([disabled])').first
        await cell.click()
        date=await page.locator('#day-label').inner_text()
        chosen=await page.locator('#selection-title').inner_text()
        await page.reload()
        await expect(page.locator('#day-label')).to_have_text(date)
        await expect(page.locator('#selection-title')).to_have_text(chosen)
        await page.goto(base+'observatory.html?window=90&expand=1&id=nonexistent-node')
        await expect(page.locator('#error')).to_contain_text('현재 스냅샷')
        await page.goto(base+'index.html?view=news&reviewed=1&limit=80')
        await expect(page.locator('#reviewed')).to_be_checked()
        await expect(page.locator('#content .card')).to_have_count(80)
        await page.locator('#content .card').first.get_by_role('link',name='이 내용의 고유 링크',exact=True).click()
        await expect(page.locator('#content .card')).to_have_count(1)
        section=page.locator('#content details').first.get_by_role('link',name='이 분석의 고유 링크')
        await section.click()
        await page.reload()
        await expect(page.locator('#content details').first).to_have_attribute('open','')
        await page.reload()
        await expect(page.locator('#content .card')).to_have_count(1)
        await page.goto(base+'knowledge.html#layer=semantic&color=community&limit=200')
        await page.locator('#mode2d').click()
        await expect(page.locator('#layer')).to_have_value('semantic')
        await expect(page.locator('#nodes g').first).to_be_visible(timeout=30000)
        title=(await page.locator('#node-list button').first.inner_text()).split(' · ',1)[1]
        await page.locator('#node-list button').first.click()
        await expect(page.locator('#detail h2')).to_have_text(title,timeout=30000)
        await page.reload()
        await expect(page.locator('#detail h2')).to_have_text(title)
        await expect(page.locator('#color')).to_have_value('community')
        await expect(page.locator('#layer')).to_have_value('semantic')
        await page.goto(base+'knowledge.html#id=nonexistent')
        await expect(page.locator('#detail h2')).to_have_text('현재 연결된 검토 지식 없음')
        await page.goto(base+'index.html?view=strategy&q=AI')
        await expect(page.locator('#search')).to_have_value('AI')
        await page.locator('#menu').get_by_role('link',name='관계 탐색',exact=True).click()
        await expect(page.locator('#search')).to_have_value('AI')
        assert 'q=AI' in page.url
        await page.get_by_role('link',name='← 전체 메뉴 · 뉴스 분석',exact=True).click()
        await expect(page.locator('#search')).to_have_value('AI')
        await page.locator('#menu').get_by_role('link',name='논문',exact=True).click()
        await expect(page.locator('#search')).to_have_value('AI')
        await page.locator('#menu').get_by_role('link',name='관측 지도',exact=True).click()
        await expect(page.locator('#theme-search')).to_have_value('AI')
        await page.locator('#search-clear').click()
        await page.get_by_role('link',name='전략 대시보드',exact=True).click()
        await expect(page.locator('#search')).to_have_value('')
        assert not any('/api/' in path for path in requests)
        assert not errors,errors
        result={'remote':remote,'menus':12,'views':12,'article_knowledge_links':True,'mirofish_menu':True,'periods':[14,30,90],'expanded':True,'all_documents':full['count'],'pagination_search_download':True,'cross_page_search':True,'mobile':True,'deep_links':True,'reload_and_back':True,'missing_targets':True,'api_requests':0,'errors':errors}
        report=Path(os.environ.get('UI_REPORT_PATH',str(ROOT/'.runtime/verification'/('public-remote-ui.json' if remote else 'public-local-ui.json'))))
        report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result))
        await browser.close()
asyncio.run(main())
