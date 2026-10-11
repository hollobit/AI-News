(() => {
  'use strict';
  addEventListener('public-data-updated', () => location.reload());
  const $ = (id) => document.getElementById(id),
    el = (tag, text, cls) => {
      const n = document.createElement(tag);
      if (text !== undefined) n.textContent = text;
      if (cls) n.className = cls;
      return n;
    };
  const names = WorkspaceNavigation.publicNames;
  let view = new URLSearchParams(location.search).get('view') || 'strategy',
    site,
    wiki,
    obs,
    limit = 40,
    archivePage = 1;
  let restoreFilters = true,
    renderToken = 0,
    splitMode = false,
    newsMetadataOnly = false,
    newsSearch = null;
  function restoreURL() {
    const p = new URLSearchParams(location.search);
    $('search').value = p.get('q') || '';
    for (const k of ['day', 'topic']) {
      const value = p.get(k) || '';
      if (value && ![...$(k).options].some((o) => o.value === value))
        $(k).add(new Option(value, value));
      $(k).value = value;
    }
    $('reviewed').checked = p.get('reviewed') === '1';
    limit = Math.max(40, Math.min(10000, Number(p.get('limit')) || 40));
    archivePage = Math.max(1, Number(p.get('page')) || 1);
  }
  function saveURL() {
    const u = new URL(location.href);
    for (const k of ['q', 'day', 'topic', 'reviewed', 'limit', 'page', 'id', 'section'])
      u.searchParams.delete(k);
    u.searchParams.set('view', view);
    for (const [k, v] of [
      ['q', $('search').value],
      ['day', $('day').value],
      ['topic', $('topic').value],
      ['reviewed', $('reviewed').checked ? '1' : ''],
      ['limit', limit > 40 ? String(limit) : ''],
      ['page', view === 'archive' && archivePage > 1 ? String(archivePage) : ''],
    ])
      if (v) u.searchParams.set(k, v);
    if (u.href !== location.href) history.pushState(null, '', u);
  }
  if (!names[view]) view = 'services';
  $('search').value = new URLSearchParams(location.search).get('q') || '';
  const url = WorkspaceNavigation.publicURL;
  const link = (text, href) => {
    const a = el('a', text);
    a.href = href;
    return a;
  };
  $('title').textContent = names[view];
  document.body.dataset.publicView = view;
  const descriptions = {
    strategy: '관측된 변화에서 시작해, 근거와 판단으로 이어가세요.',
    news: '원출처와 검토된 분석을 함께 읽고, 관련 지식으로 연결하세요.',
    archive: '같은 뉴스 자료를 날짜별로 탐색합니다. URL 수정 이력은 별도 기능입니다.',
    research: '분석이 연결된 뉴스를 모았습니다. 종합 보고서는 통합 분석실에서 확인하세요.',
    papers: '연구의 주장과 확인된 근거를 읽고, 다음 질문을 찾아보세요.',
    risks: '현재 위험과 조건부 전망을 구분해 읽고, 성립 조건과 불확실성을 확인하세요.',
    wiki: '검토된 지식을 연결하고, 판단을 뒷받침하는 원자료를 확인하세요.',
    sources: '뉴스의 원출처 목록입니다. 원문 읽기·구독·복구는 실시간에서 이어집니다.',
  };
  $('public-intro').textContent =
    descriptions[view] || '공개 자료에서 실시간 작업 공간까지, 같은 흐름으로 이어집니다.';
  document.title = names[view] + ' · AI 뉴스';
  function safe(value) {
    try {
      const u = new URL(value);
      return ['http:', 'https:'].includes(u.protocol) ? u.href : '';
    } catch {
      return '';
    }
  }
  function external(text, value) {
    const u = safe(value);
    if (!u) return el('span', '공개 출처 링크 없음');
    const a = link(text, u);
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    return a;
  }
  function card(title, meta) {
    const n = el('article', undefined, 'card');
    n.append(el('small', meta, 'tag'), el('h2', title));
    return n;
  }
  function article(a) {
    const n = card(
      a.title,
      `${a.day} · ${a.topic} · ${a.analyses.length ? '현재 입력·독립 검토 확인' : '제목·출처만 공개'}`
    );
    if (a.title_origin) {
      n.append(
        el(
          'small',
          a.title_origin === 'source_page'
            ? '확보된 원문 제목'
            : 'Telegram 브리핑 제목 · 원문 제목 미확인',
          'tag'
        )
      );
      if (a.source_title && a.briefing_title !== a.source_title)
        n.append(el('p', '브리핑 제목: ' + a.briefing_title));
    }
    for (const r of a.analyses) {
      const d = el('details');
      d.append(el('summary', r.kind + (r.title ? ' · ' + r.title : '')), el('p', r.text));
      if (r.uncertainty) d.append(el('p', '불확실성: ' + r.uncertainty));
      n.append(d);
    }
    const links = el('div', undefined, 'links');
    links.append(
      external('원출처', a.url),
      link(
        '지식 연결',
        'knowledge.html#' + new URLSearchParams({ article_id: a.id, source_url: a.url })
      ),
      link('관련 자료 종합 탐색 →', WorkspaceNavigation.href('context', true, { q: a.title })),
      link(
        '실시간 기사 상세 ↗',
        WorkspaceNavigation.gateway(
          'article',
          {},
          'https://workspace.invalid/?' + new URLSearchParams({ url: a.url })
        )
      )
    );
    n.append(links);
    return n;
  }
  function paper(p) {
    const n = card(p.title, `${p.id} · ${p.day} · ${p.status} · ${p.provider}`);
    if (p.summary) n.append(el('p', p.summary));
    for (const c of p.claims) {
      const d = el('details');
      d.append(el('summary', c.title), el('p', c.detail), el('p', '불확실성: ' + c.uncertainty));
      n.append(d);
    }
    n.append(
      external('논문 원출처', p.url),
      document.createTextNode(' · '),
      link('지식 연결', 'knowledge.html#id=' + encodeURIComponent('source:paper:' + p.id)),
      document.createTextNode(' · '),
      link(
        '논문 상세·관련 연구 ↗',
        WorkspaceNavigation.gateway(
          'papers',
          {},
          'https://workspace.invalid/?' + new URLSearchParams({ paper: p.id })
        )
      )
    );
    return n;
  }
  function wikiPage(p) {
    const n = card(p.title, p.kind + ' · 검토 판 ' + p.revision);
    for (const c of p.claims) {
      n.append(el('p', c.text));
      for (const id of c.evidence_ids) {
        const source = wiki.nodes.find((n) => n.id === id);
        if (source?.url) n.append(external('인용 출처 · ' + source.title, source.url));
      }
    }
    const node = wiki.nodes.find((n) => (n.page_ids || []).includes(p.id));
    if (node) n.append(link('관계 탐색', 'knowledge.html#id=' + encodeURIComponent(node.id)));
    return n;
  }
  function searchText(item) {
    if (item._compact && newsSearch) return (newsSearch[item.id] || '').toLocaleLowerCase();
    return [
      item.title,
      item.source_text,
      item.topic,
      item.url,
      item.summary,
      item.current_basis,
      item.scenario,
      item.uncertainty,
      ...(item.assumptions || []),
      ...(item.mitigations || []),
      ...(item.analyses || []).flatMap((a) => [a.title, a.text, a.uncertainty]),
      ...(item.claims || []).flatMap((c) => [c.title, c.text, c.detail, c.uncertainty]),
    ]
      .filter(Boolean)
      .join('\n')
      .toLocaleLowerCase();
  }
  function appendNetwork(parent, items, centerLabel, title, description) {
    if (!window.NewsNetwork) return;
    const section = el('section', undefined, 'news-network-section');
    const intro = el('div', undefined, 'news-network-intro');
    const wrap = document.createElement('div');
    wrap.append(el('h3', title), el('p', description));
    intro.append(wrap);
    const target = document.createElement('div');
    section.append(intro, target);
    parent.append(section);
    window.NewsNetwork.render(target, items, { centerLabel, limit: 48 });
  }
  function appendRiskNetwork(parent) {
    const data = site.risk_graph;
    if (!data?.nodes?.length || !window.RiskNetwork) return;
    const day = $('day').value;
    let scoped = data;
    const connect = (selected) => {
      const connected = new Set(selected);
      (data.edges || []).forEach((e) => {
        if (selected.has(e.source)) connected.add(e.target);
        if (selected.has(e.target)) connected.add(e.source);
      });
      const nodes = data.nodes.filter((n) => connected.has(n.id)),
        edges = (data.edges || []).filter(
          (e) => connected.has(e.source) && connected.has(e.target)
        );
      return {
        ...data,
        nodes,
        edges,
        coverage: {
          ...(data.coverage || {}),
          risks: selected.size,
          source_documents: nodes.filter((n) => n.kind === 'evidence').length,
          graphrag_nodes: nodes.filter((n) => n.kind === 'graphrag').length,
          relationships: edges.length,
        },
      };
    };
    if (day) {
      const selected = new Set(
        data.nodes
          .filter((n) => n.kind === 'risk' && ((n.days || []).includes(day) || n.day === day))
          .map((n) => n.id)
      );
      scoped = connect(selected);
    } else {
      const selected = new Set(
        data.nodes
          .filter((n) => n.kind === 'risk')
          .sort((a, b) => (b.weight || 0) - (a.weight || 0))
          .slice(0, 80)
          .map((n) => n.id)
      );
      scoped = connect(selected);
    }
    const section = el('section', undefined, 'risk-network-section');
    const intro = el('div', undefined, 'news-network-intro');
    const wrap = document.createElement('div');
    wrap.append(
      el('h3', '위험·조건부 시나리오 GraphRAG 연결 지도'),
      el(
        'p',
        day
          ? `${day}의 근거와 연결된 위험·시나리오만 표시합니다. 선택 날짜의 근거가 없는 항목은 지도에서 제외됩니다. 가중치는 검토 우선순위와 유사성 기반이며 발생 확률이나 인과관계를 뜻하지 않습니다.`
          : '전체 원장 중 연결 가중치가 높은 위험을 요약 표시합니다. 날짜를 선택하면 해당 날짜의 위험과 관련 근거로 지도가 다시 좁혀집니다.'
      )
    );
    intro.append(wrap);
    const summary = el('div', undefined, 'risk-network-summary');
    summary.id = 'risk-network-summary';
    const target = el('div', undefined, 'risk-network');
    target.id = 'risk-network';
    target.append(el('p', '관계 지도를 불러오는 중입니다.', 'subtle'));
    const detail = el('p', undefined, 'risk-network-detail');
    detail.id = 'risk-network-detail';
    section.append(intro, summary, target, detail);
    parent.append(section);
    try {
      window.RiskNetwork.render(scoped);
    } catch (error) {
      target.replaceChildren(
        el(
          'p',
          '관계 지도는 현재 스냅샷을 불러오지 못했습니다. 아래 위험 분석 목록은 계속 확인할 수 있습니다.',
          'empty'
        )
      );
    }
  }
  function similarRiskBlock(r) {
    const current = (site.risk_graph.nodes || []).find(
      (n) => n.kind === 'risk' && (n.label === r.title || n.name === r.title)
    );
    const block = el('div', undefined, 'risk-similar-block');
    block.append(
      el('h3', '유사 위험·공통 검토 근거'),
      el(
        'p',
        '같은 검토 근거를 공유하는 항목입니다. 검색 유사성·근거 연결을 보여줄 뿐, 인과관계나 발생 확률을 의미하지 않습니다.',
        'muted'
      )
    );
    if (!current) {
      block.append(el('p', '연결된 GraphRAG 위험 정보가 없습니다.', 'empty'));
      return block;
    }
    const related = [];
    for (const e of site.risk_graph.edges || []) {
      if (
        e.relation !== '공유 검토 근거 · 유사성' ||
        (e.source !== current.id && e.target !== current.id)
      )
        continue;
      const id = e.source === current.id ? e.target : e.source,
        n = (site.risk_graph.nodes || []).find((item) => item.id === id && item.kind === 'risk');
      if (n) related.push({ edge: e, node: n });
    }
    related.sort(
      (a, b) =>
        (b.edge.similarity || 0) - (a.edge.similarity || 0) ||
        (b.node.weight || 0) - (a.node.weight || 0)
    );
    if (!related.length) {
      block.append(el('p', '공유 검토 근거가 있는 유사 위험이 없습니다.', 'empty'));
      return block;
    }
    const list = el('ul', 'risk-similar-list');
    related.slice(0, 8).forEach(({ edge, node: n }) => {
      const li = el(
        'li',
        undefined,
        `${n.label || n.name} · 유사성 ${edge.similarity ?? '미상'} · 가중치 ${n.weight ?? '미상'} · 공유 근거 ${n.shared_evidence_count ?? 0}개`
      );
      const a = link(
        '위험 목록에서 보기',
        'index.html?' + new URLSearchParams({ view: 'risks', q: n.label || n.name })
      );
      li.append(document.createTextNode(' '), a);
      list.append(li);
    });
    block.append(list);
    if (related.length > 8)
      block.append(el('p', `유사 위험 ${related.length}개 중 상위 8개를 표시합니다.`, 'muted'));
    return block;
  }
  function matching(item) {
    const q = $('search').value.trim().toLocaleLowerCase();
    return (
      ObservatorySearch.matches(ObservatorySearch.parse(q), [searchText(item)]) &&
      (!$('day').value || item.day === $('day').value) &&
      (!$('topic').value || item.topic === $('topic').value)
    );
  }
  function dashboardTopics() {
    const q = $('search').value.trim().toLocaleLowerCase(),
      day = $('day').value,
      topic = $('topic').value,
      reviewed = $('reviewed').checked;
    const eligible = new Set(
      site.news
        .filter(
          (n) =>
            (!day || n.day === day) &&
            (!topic || n.topic === topic) &&
            (!reviewed || n.analyses.length)
        )
        .map((n) => n.url)
        .filter(Boolean)
    );
    const labels = new Map((obs.nodes || []).map((n) => [n.id, n.label]));
    return (obs.nodes || []).filter((n) => {
      if (n.kind !== 'topic' || n.count <= 0) return false;
      if (day && (n.series[obs.days.indexOf(day)] || 0) <= 0) return false;
      const evidence = (n.evidence_by_day || [])
        .flatMap((ids, i) => (!day || obs.days[i] === day ? ids : []))
        .map((id) => obs.evidence?.[id])
        .filter(Boolean);
      if ((topic || reviewed) && !evidence.some((e) => eligible.has(e.url))) return false;
      const neighbors = (obs.edges || [])
        .filter((e) => e.source === n.id || e.target === n.id)
        .map((e) => labels.get(e.source === n.id ? e.target : e.source));
      return ObservatorySearch.matches(
        ObservatorySearch.parse(q),
        [n.label, ...neighbors, ...evidence.map((e) => e.title)].filter(Boolean)
      );
    });
  }
  async function render() {
    const token = ++renderToken;
    if (restoreFilters) {
      restoreURL();
      restoreFilters = false;
    }
    try {
      const parsedSearch = ObservatorySearch.parse($('search').value);
      $('search').setAttribute('aria-invalid', String(!!parsedSearch.error));
      if (parsedSearch.error) {
        $('content').replaceChildren();
        $('result-count').textContent = parsedSearch.error;
        return;
      }
      if (
        newsMetadataOnly &&
        ($('search').value.trim() ||
          $('day').value ||
          $('topic').value ||
          $('reviewed').checked ||
          new URLSearchParams(location.search).has('id'))
      ) {
        site.news = await PublicData.news();
        newsMetadataOnly = false;
      }
      if (splitMode && $('search').value.trim() && site.news.length && !newsSearch)
        newsSearch = await PublicData.searchNews();
      if (token !== renderToken) return;
      const box = $('content');
      box.replaceChildren();
      let items = [],
        draw = article;
      if (view === 'simulation') {
        const intro = card(
          'MiroFish 전략 시뮬레이션',
          '가정 기반 탐색 · 실제 예측이나 검증된 사실과 구분'
        );
        intro.append(
          el(
            'p',
            '뉴스와 검토 근거를 살펴본 뒤, 로그인한 작업 공간에서 가설을 구성하고 시뮬레이션을 실행합니다.'
          )
        );
        const launch = link(
          '실시간 시뮬레이션 열기 ↗',
          WorkspaceNavigation.href('simulation', true, {
            q: $('search').value,
            date: $('day').value,
            topic: $('topic').value,
          })
        );
        launch.id = 'mirofish-remote';
        intro.append(
          launch,
          el(
            'p',
            '입력한 검색 조건을 이어갑니다. 실행은 로그인 후 직접 시작하며, 시뮬레이션 결과는 검토된 사실이나 발생 확률과 구분합니다.',
            'muted'
          )
        );
        box.append(intro, el('h2', '분석에 사용할 뉴스 검색'));
        items = site.news;
      }
      if (view === 'services') {
        box.append(
          card('하나의 작업 공간, 두 가지 이용 모드', '공개 자료 조회 · 로그인 후 실시간 분석'),
          el(
            'p',
            '공개 스냅샷은 게시 시점의 뉴스·검토 분석·논문·지식과 관측 지도를 제공합니다. 화면 상단에서 자료 기준 시각을 확인할 수 있습니다.'
          ),
          el(
            'p',
            '실시간으로 열기를 선택하면 같은 메뉴의 최신 서버 화면으로 이어집니다. 자연어 종합 질의, 검토된 전망, 전략 선택, 시뮬레이션과 수집·분석 운영을 로그인 후 사용할 수 있습니다.'
          ),
          card('자료를 비교할 때 확인할 기준', '날짜 · 범위 · 단위 · 검토 상태'),
          el(
            'p',
            '고유 뉴스 수와 수정 이력을 포함한 URL 기록 수는 다릅니다. 기사 날짜, 분석 갱신일, 관측 기간과 공개 게시 시각을 구분합니다. 기본 분석이 연결됐다는 표시가 상세 분석 완료를 뜻하지는 않습니다.'
          ),
          card('빠르게 이동하기', '⌘/Ctrl K · 작업 공간 · 세부 기능 탭'),
          el(
            'p',
            '빠른 찾기에서 기능 이름을 검색하거나 질문을 입력해 분석실로 이어가세요. 최근 사용한 기능은 최대 6개를 우선 표시합니다. 메뉴 이동으로 새로운 분석을 자동 실행하지 않습니다.'
          ),
          link(
            '통합 분석실 열기 →',
            WorkspaceNavigation.href('context', true, { q: $('search').value })
          )
        );
        $('result-count').textContent = '';
        $('more').hidden = true;
        return;
      }
      if (view === 'strategy') {
        const selectedTopics = dashboardTopics(),
          grid = el('div', undefined, 'grid');
        grid.id = 'dashboard-topics';
        box.append(el('h2', `관련 전략 주제 ${selectedTopics.length}개`));
        box.append(
          el(
            'p',
            '주제 이름·연결 키워드·공개 근거 표본에서 찾습니다. 날짜·분류·검토 필터도 적용합니다. 곡선과 건수는 해당 주제의 전체 관측 집계이며 검색 결과만의 재집계가 아닙니다.',
            'muted'
          )
        );
        for (const t of selectedTopics) {
          const n = card(
            t.label,
            `주제 전체 ${obs.comparison_days}일 고유 문서 ${t.current} · 이전 ${t.previous}${t.previous === 0 ? ' · 신규 관측/비교 한계' : ''}`
          );
          const bars = el('div', undefined, 'bars'),
            max = Math.max(1, ...t.series);
          t.series.forEach((v, i) => {
            const b = el('i');
            b.style.height = (v / max) * 80 + 'px';
            b.title = obs.days[i] + ' · ' + v;
            bars.append(b);
          });
          n.append(
            bars,
            link(
              '곡선·근거·관계 보기',
              'observatory.html?' +
                new URLSearchParams({
                  window: String(obs.comparison_days || 14),
                  id: t.id,
                  expand: obs.limits?.expanded ? '1' : '0',
                  ...($('day').value ? { date: $('day').value } : {}),
                })
            )
          );
          grid.append(n);
        }
        if (!selectedTopics.length)
          grid.append(el('p', '현재 검색·필터에 일치하는 전략 주제가 없습니다.', 'empty'));
        box.append(grid);
        box.append(
          el(
            'h2',
            $('search').value.trim() ? '검색 결과 뉴스와 검토 분석' : '최근 뉴스와 검토 분석'
          )
        );
        items = site.news;
      } else if (view === 'risks') {
        items = site.risks || [];
        draw = (r) => {
          const n = card(
            r.title,
            '기사 ' +
              (r.day || '날짜 미상') +
              ' · 분석 ' +
              (r.analysis_at ? r.analysis_at.slice(0, 10) : '날짜 미상') +
              ' · 검토된 해석 · 현재 위험 ' +
              ({ unknown: '미상', low: '낮음', moderate: '보통', high: '높음', critical: '심각' }[
                r.current_severity
              ] || '미상')
          );
          const outlook = el('div', undefined, 'public-risk-outlook');
          outlook.append(
            el(
              'span',
              '미래 가능성 · ' +
                ({ unknown: '산정 불가', low: '낮음', moderate: '보통', high: '높음' }[
                  r.future_likelihood
                ] || '산정 불가')
            ),
            el(
              'span',
              '예상 범위 · ' +
                ({ '0-3mo': '0–3개월', '3-12mo': '3–12개월', '12-36mo': '1–3년' }[r.horizon] ||
                  '미상')
            )
          );
          n.append(outlook);
          for (const [k, label] of [
            ['current_basis', '현재 판단 근거'],
            ['scenario', '조건부 미래 시나리오'],
            ['assumptions', '전제'],
            ['uncertainty', '불확실성'],
            ['mitigations', '완화 방안'],
          ]) {
            n.append(el('h3', label), el('p', Array.isArray(r[k]) ? r[k].join('\n') : r[k] || ''));
          }
          n.append(
            similarRiskBlock(r),
            external('인용 출처', r.url),
            document.createTextNode(' · '),
            link('관련 뉴스 분석', 'index.html?view=news&id=' + r.article_id)
          );
          return n;
        };
        appendRiskNetwork(box);
      } else if (view === 'papers') {
        items = site.papers;
        draw = paper;
      } else if (view === 'wiki') {
        items = wiki.pages;
        draw = wikiPage;
      } else if (view === 'sources') {
        const map = new Map(site.news.filter((a) => a.url).map((a) => [a.url, a]));
        items = [...map.values()];
        draw = (a) => {
          const n = card(a.title, a.day);
          n.append(external(a.url, a.url));
          return n;
        };
      } else if (view !== 'simulation')
        items = site.news.filter((a) => view !== 'research' || a.analyses.length);
      items = items
        .filter(matching)
        .filter(
          (a) =>
            !$('reviewed').checked ||
            ['wiki', 'risks'].includes(view) ||
            a.analyses?.length ||
            a.status === '검토 완료'
        );
      if (view === 'strategy')
        appendNetwork(
          box,
          items,
          '최근 뉴스와 검토 분석',
          '최근 뉴스와 검토 분석 연결망',
          '중앙 범위에서 주제로, 주제에서 뉴스로 이어집니다. 선은 분류·공동 범위를 보여주며 인과관계가 아닙니다.'
        );
      if (view === 'archive')
        appendNetwork(
          box,
          items,
          $('day').value || '전체 날짜',
          '날짜별 뉴스 연결망',
          '선택한 날짜 또는 전체 기간의 뉴스·주제·검토 상태를 연결해 봅니다. 연결망은 탐색용이며 인과관계를 뜻하지 않습니다.'
        );
      if (view === 'risks') items = items.map((i) => ({ ...i, id: i.article_id + ':' + i.title }));
      const requested = new URLSearchParams(location.search).get('id');
      if (requested) items = items.filter((i) => i.id === requested);
      const archivePaging = view === 'archive' && !requested,
        archiveTotalPages = Math.max(1, Math.ceil(items.length / 40));
      archivePage = Math.min(archivePage, archiveTotalPages);
      let displayItems = archivePaging
        ? items.slice((archivePage - 1) * 40, archivePage * 40)
        : items.slice(0, limit);
      if (splitMode && displayItems.some((i) => i._compact) && view !== 'sources') {
        const details = new Map(
          (await PublicData.articles(displayItems.map((i) => i.id))).map((i) => [i.id, i])
        );
        if (token !== renderToken) return;
        displayItems = displayItems.map((i) => details.get(i.id) || i);
      }
      $('result-count').textContent =
        `공개 자료 ${items.length.toLocaleString()}개 · ${displayItems.length.toLocaleString()}개 표시` +
        (archivePaging
          ? ` · 전체 날짜 ${archivePage} / ${archiveTotalPages}페이지 · 40개 단위`
          : '');
      let day = '';
      for (const item of displayItems) {
        if (view === 'archive' && item.day !== day) {
          day = item.day;
          box.append(el('h2', day));
        }
        const n = draw(item),
          params = new URLSearchParams({ view, id: item.id });
        if ($('search').value.trim() && item.source_text) {
          const terms = parsedSearch.groups.flat().filter((t) => !t.negative);
          const normalized = item.source_text.normalize('NFKC').toLocaleLowerCase();
          const positions = terms.map((t) => normalized.indexOf(t.value)).filter((i) => i >= 0);
          if (positions.length) {
            const start = Math.max(0, Math.min(...positions) - 65);
            n.append(
              el(
                'p',
                '원문 일치 · ' + (start ? '…' : '') + item.source_text.slice(start, start + 260)
              )
            );
          }
        }
        ObservatorySearch.highlight(n, parsedSearch);
        for (const detail of n.querySelectorAll('details'))
          if (detail.querySelector('mark')) detail.open = true;
        n.append(el('p'), link('이 내용의 고유 링크', 'index.html?' + params));
        for (const [i, d] of [...n.querySelectorAll('details')].entries()) {
          const section = String(i);
          const p = new URLSearchParams(params);
          p.set('section', section);
          d.append(link('이 분석의 고유 링크', 'index.html?' + p));
          if (
            requested &&
            (!new URLSearchParams(location.search).has('section') ||
              new URLSearchParams(location.search).get('section') === section)
          )
            d.open = true;
          d.querySelector('summary').addEventListener('click', () =>
            setTimeout(() => {
              const u = new URL(location.href);
              u.search = d.open ? p : params;
              history.replaceState(null, '', u);
            }, 0)
          );
        }
        box.append(n);
      }
      $('more').hidden = view === 'archive' || limit >= items.length;
      $('archive-pagination').hidden = !archivePaging || archiveTotalPages <= 1;
      $('archive-page-number').textContent = archivePaging
        ? `${archivePage} / ${archiveTotalPages} 페이지`
        : '';
      $('archive-prev').disabled = !archivePaging || archivePage <= 1;
      $('archive-next').disabled = !archivePaging || archivePage >= archiveTotalPages;
    } catch (e) {
      if (token === renderToken) $('notice').textContent = e.message;
    }
  }
  async function load() {
    try {
      if (window.PublicData) {
        try {
          const manifest = await PublicData.manifest();
          splitMode = true;
          site = {
            news: [],
            papers: [],
            risks: [],
            risk_graph: { nodes: [], edges: [] },
            coverage: manifest.coverage,
            exported_at: manifest.exported_at,
          };
          wiki = { pages: [], nodes: [] };
          obs = { nodes: [], edges: [], days: [] };
          if (!['papers', 'wiki', 'services'].includes(view)) {
            newsMetadataOnly = view === 'strategy' && !!manifest.news.catalog;
            site.news = await PublicData.news({ metadataOnly: newsMetadataOnly });
          }
          if (view === 'papers') site.papers = await PublicData.section('papers');
          if (view === 'wiki') wiki = await PublicData.section('wiki');
          if (view === 'risks') {
            [site.risks, site.risk_graph] = await Promise.all([
              PublicData.section('risks'),
              PublicData.section('risk_graph'),
            ]);
          }
          if (view === 'strategy') obs = await PublicData.section('observatory');
        } catch (e) {
          if (splitMode) throw e;
        }
      }
      if (!splitMode)
        [site, wiki, obs] = await Promise.all(
          ['site.json', 'knowledge.json', 'observatory-14-default.json'].map(async (path) => {
            const r = await fetch(path);
            if (!r.ok) throw Error('공개 자료 조회 실패');
            return r.json();
          })
        );
      $('notice').textContent =
        '읽기 전용 공개 스냅샷 · ' +
        new Date(site.exported_at).toLocaleString('ko-KR') +
        ' · 원문 발췌 제외 · 전체 분석 완료를 뜻하지 않습니다.';
      for (const [key, label] of [
        ['news', '고유 뉴스'],
        ['reviewed_news', '검토 분석 연결 뉴스'],
        ['papers', '메타데이터 확보 논문'],
        ['reviewed_papers', '현재 검토 논문'],
      ]) {
        const n = el('article', undefined, 'metric');
        n.append(el('small', label), el('strong', site.coverage[key].toLocaleString()));
        $('metrics').append(n);
      }
      const items = view === 'papers' ? site.papers : site.news;
      for (const day of [...new Set(items.map((a) => a.day).filter(Boolean))].sort().reverse())
        $('day').add(new Option(day, day));
      for (const topic of [...new Set(items.map((a) => a.topic).filter(Boolean))].sort())
        $('topic').add(new Option(topic, topic));
      await render();
    } catch (e) {
      $('notice').textContent = e.message;
    }
  }
  for (const id of ['search', 'day', 'topic', 'reviewed'])
    $(id).addEventListener(id === 'search' ? 'input' : 'change', () => {
      limit = 40;
      archivePage = 1;
      saveURL();
      render();
    });
  $('more').onclick = () => {
    limit += 40;
    saveURL();
    render();
  };
  $('archive-prev').onclick = () => {
    if (archivePage > 1) {
      archivePage--;
      saveURL();
      render();
    }
  };
  $('archive-next').onclick = () => {
    archivePage++;
    saveURL();
    render();
  };
  addEventListener('popstate', () => {
    restoreFilters = true;
    render();
  });
  load();
})();
