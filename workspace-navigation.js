/* One destination registry for the live workspace and its public snapshot. */
((root) => {
  'use strict';
  const spaces = [
    [
      'strategy',
      '전략 대시보드',
      '변화의 흐름을 읽고, 중요한 신호부터 확인하세요.',
      'strategy',
      'pulse',
    ],
    [
      'analysis',
      '통합 분석실',
      '뉴스와 논문, 위험과 지식을 하나의 질문으로 연결합니다.',
      'context',
      'spark',
    ],
    ['news', '뉴스·자료', '브리핑에서 원출처와 수정 이력까지 이어 읽습니다.', 'news', 'news'],
    ['papers', '논문·연구', '연구의 발견과 실제 도입의 근거를 함께 살펴봅니다.', 'papers', 'paper'],
    [
      'knowledge',
      '지식',
      '검토된 해석을 쌓고, 주장과 출처의 연결을 탐색합니다.',
      'wiki',
      'network',
    ],
    [
      'risks',
      '위험·전망',
      '현재의 위험과 조건부 미래, 판단의 변화를 구분합니다.',
      'risks',
      'shield',
    ],
    [
      'decisions',
      '전략·실험',
      '대응 선택지를 비교하고, 가설을 실험으로 이어갑니다.',
      'decisions',
      'compass',
    ],
    [
      'operations',
      '운영센터',
      '수집부터 독립 검토까지, 작업과 문제를 한눈에 확인합니다.',
      'workflow',
      'activity',
    ],
  ].map(([id, label, description, home, icon]) => ({ id, label, description, home, icon }));
  // Public destinations are explicit: an archive is not a URL edit ledger,
  // and the knowledge map is not the executable evidence graph.
  const rows = [
    [
      'strategy',
      '전략 변화',
      '/strategy',
      'strategy',
      'index.html?view=strategy',
      '모멘텀 · 중요 뉴스 · 관측 신호',
    ],
    [
      'observatory',
      '관측 지도',
      '/observatory',
      'strategy',
      'observatory.html',
      '14·30·90일 곡선 · 관계 · 히트맵',
    ],
    ['intelligence', '전략 검토', '/intelligence', 'strategy', '', '중요 변화 · 근거 · 판단 기준'],
    ['topics', '주제 종합', '/intelligence/topics', 'strategy', '', '주제별 변화와 이견'],
    ['context', '종합 질의', '/context', 'analysis', '', '자연어 질문 · 비교 · 독립 근거 검토'],
    ['graph', '근거 그래프', '/graph', 'analysis', '', '엔티티 · 관계 · 근거 질문'],
    ['research', '종합 보고서', '/research', 'analysis', '', '국가·기업 전략 · 분석 실행 이력'],
    [
      'news',
      '뉴스 탐색',
      '/news',
      'news',
      'index.html?view=news',
      '핵심 브리핑 · 원문 · 검토 분석',
    ],
    [
      'timeline',
      '날짜별 보기',
      '/news?view=links',
      'news',
      'index.html?view=archive',
      '기간별 뉴스와 반복 관측',
    ],
    ['archive', 'URL 수정 이력', '/archive', 'news', '', '원래 제목 · URL · 수정 전 기록'],
    [
      'sources',
      '외부 자료',
      '/sources',
      'news',
      'index.html?view=sources',
      '원출처 목록 · 원문 읽기 · 근거 보강',
    ],
    ['events', '사건과 출처', '/intelligence/events', 'news', '', '같은 사건 · 독립 확인 근거'],
    [
      'papers',
      '논문 탐색',
      '/papers',
      'papers',
      'index.html?view=papers',
      '논문 · 연구 동향 · 검토 결과',
    ],
    [
      'adoption',
      '연구에서 도입까지',
      '/intelligence/research',
      'papers',
      '',
      '재현 · 실증 · 도입 근거',
    ],
    [
      'wiki',
      '지식 위키',
      '/wiki',
      'knowledge',
      'index.html?view=wiki',
      '주제 · 검토된 주장 · 변경 이력',
    ],
    [
      'knowledge',
      '지식 관계 지도',
      '/knowledge',
      'knowledge',
      'knowledge.html',
      '2D/3D 탐색 · 주장과 출처',
    ],
    [
      'concepts',
      '개념 사전',
      '/intelligence/concepts',
      'knowledge',
      '',
      '전략 용어 · 별칭 · 연결 근거',
    ],
    [
      'risks',
      '현재 위험',
      '/risks',
      'risks',
      'index.html?view=risks',
      '평가 · 검토 우선순위 · 반대 근거',
    ],
    [
      'forecast',
      '조건부 전망',
      '/context#forecast-panel',
      'risks',
      '',
      '예상 시기 · 확률 범위 · 성립 조건',
    ],
    [
      'risk-history',
      '평가 변경 이력',
      '/intelligence/risk_history',
      'risks',
      '',
      '원문 변화 · 정정 · 철회',
    ],
    [
      'decisions',
      '전략 선택',
      '/intelligence/decisions',
      'decisions',
      '',
      '선택지 · 비용 · 재검토 조건',
    ],
    [
      'scenarios',
      '가설 비교',
      '/intelligence/scenarios',
      'decisions',
      '',
      '가정 · 관측 조건 · 시나리오 비교',
    ],
    ['simulation', '시뮬레이션', '/simulation', 'decisions', '', 'MiroFish 실행 · 저장된 실험'],
    [
      'experiments',
      '개선 실험',
      '/intelligence/experiments',
      'decisions',
      '',
      '기존 규칙과 후보 규칙 비교',
    ],
    [
      'profiles',
      '판단 기준',
      '/intelligence/profiles',
      'decisions',
      '',
      '중요도 · 위험 · 근거 가중치',
    ],
    ['workflow', '실시간 시황판', '/workflow', 'operations', '', '단계별 히트맵 · 개별 작업 진행'],
    [
      'operations',
      '수집·분석 관리',
      '/operations',
      'operations',
      '',
      '시작 · 일시중지 · 재개 · 복구',
    ],
    [
      'subscriptions',
      '출처·구독 관리',
      '/sources#subscription-form',
      'operations',
      '',
      '정기 관측 · 원문 복구 · 출처 상태',
    ],
    ['quality', '보완 작업', '/intelligence/operations', 'operations', '', '검토 사유 · 처리 이력'],
    [
      'services',
      '이용 안내',
      '',
      '',
      'index.html?view=services',
      '공개 조회 · 로그인 분석 · 자료 기준',
    ],
    ['article', '기사 상세', '/article', 'news', '', '기사 해설과 원출처'],
  ];
  const entries = rows.map(([id, label, path, space, snapshot, description]) => ({
    id,
    label,
    path,
    space,
    snapshot,
    description,
  }));
  const byId = Object.assign(
    Object.create(null),
    Object.fromEntries(entries.map((e) => [e.id, e]))
  );
  const publicNames = {
    strategy: '전략 대시보드',
    news: '뉴스 탐색',
    archive: '날짜별 보기',
    research: '분석 있는 뉴스',
    risks: '위험·조건부 전망',
    papers: '논문·연구',
    wiki: '지식 위키',
    graph: '지식 관계 지도',
    sources: '원출처 목록',
    simulation: '전략 시뮬레이션',
    services: '이용 안내',
    context: '통합 분석실',
  };
  const publicIds = { archive: 'timeline', research: 'news', graph: 'knowledge' };
  const publicURL = (id) =>
    id === 'context'
      ? 'context.html'
      : id === 'graph'
        ? 'knowledge.html'
        : id === 'observatory'
          ? 'observatory.html'
          : 'index.html?view=' + encodeURIComponent(id);
  const extraKeys = {
    context: ['answer', 'forecast', 'node', 'question'],
    forecast: ['answer', 'forecast', 'question'],
    graph: ['channel', 'content_type'],
    knowledge: [
      'q',
      'id',
      'source_url',
      'paper_id',
      'layer',
      'color',
      'view',
      'limit',
      'az',
      'el',
      'dist',
      'tx',
      'ty',
      'tz',
      'zoom',
      'x',
      'y',
    ],
    wiki: ['id', 'source_url'],
    observatory: ['id', 'expand', 'filter', 'log', 'docq', 'docs'],
    papers: ['paper', 'category', 'sector', 'status', 'window_days'],
    research: ['run', 'document'],
    simulation: ['run', 'hypothesis'],
    risks: ['risk', 'sort', 'status', 'domain', 'severity', 'likelihood'],
    archive: ['active'],
    article: ['url'],
  };
  function identify(href, isPublic = false) {
    const u = new URL(href, 'https://workspace.invalid/');
    if (isPublic) {
      if (u.pathname.endsWith('context.html'))
        return byId[u.searchParams.get('destination')]
          ? u.searchParams.get('destination')
          : 'context';
      if (u.pathname.endsWith('knowledge.html') || u.hash.includes('view=3d')) return 'knowledge';
      if (u.pathname.endsWith('observatory.html')) return 'observatory';
      const view = u.searchParams.get('view') || 'strategy';
      return Object.hasOwn(publicIds, view) ? publicIds[view] : byId[view] ? view : 'services';
    }
    if (u.pathname === '/context' && u.hash === '#forecast-panel') return 'forecast';
    if (u.pathname === '/sources' && u.hash === '#subscription-form') return 'subscriptions';
    if (u.pathname === '/news' && u.searchParams.get('view') === 'links') return 'timeline';
    if (u.pathname === '/') return u.search ? 'news' : 'strategy';
    if (u.pathname === '/strategy' && ['#baseline', '#workflow', '#improvement'].includes(u.hash))
      return 'operations';
    return (
      entries.find((e) => e.path && new URL(e.path, u).pathname === u.pathname)?.id || 'strategy'
    );
  }
  function context(href) {
    const u = new URL(href, 'https://workspace.invalid/'),
      p = u.searchParams;
    const h = new URLSearchParams(u.hash.slice(1));
    return {
      q:
        p.get('ws_query') ||
        h.get('ws_query') ||
        p.get('q') ||
        h.get('q') ||
        p.get('question') ||
        '',
      date: p.get('date') || p.get('day') || p.get('ws_date') || h.get('ws_date') || '',
      topic: p.get('topic') || p.get('ws_topic') || h.get('ws_topic') || '',
      window: p.get('window') || '',
    };
  }
  function parameters(id, value = {}, isPublic = false) {
    const p = new URLSearchParams();
    const q = String(value.q || '')
      .trim()
      .slice(0, 2000);
    if (q) p.set(q.length > 256 && !['context', 'forecast'].includes(id) ? 'ws_query' : 'q', q);
    if (/^\d{4}-\d{2}-\d{2}$/.test(value.date || '')) {
      const supports = [
        'strategy',
        'news',
        'timeline',
        'archive',
        'graph',
        'observatory',
        'simulation',
      ].includes(id);
      p.set(supports ? (isPublic && id !== 'observatory' ? 'day' : 'date') : 'ws_date', value.date);
    }
    if (value.topic)
      p.set(
        ['strategy', 'news', 'timeline', 'graph', 'simulation'].includes(id) ? 'topic' : 'ws_topic',
        String(value.topic).slice(0, 200)
      );
    if (id === 'observatory' && ['14', '30', '90'].includes(String(value.window)))
      p.set('window', value.window);
    return p;
  }
  function href(id, isPublic = false, value = {}) {
    const e = byId[id] || byId.context;
    const gateway = isPublic && !e.snapshot;
    const target = isPublic ? e.snapshot || 'context.html' : e.path || '/strategy';
    const u = new URL(target, 'https://workspace.invalid/');
    if (gateway && e.id !== 'context') u.searchParams.set('destination', e.id);
    // Gateway context uses the live parameter names; day/date translation is
    // performed once when leaving the public projection.
    const p = parameters(e.id, value, isPublic && !gateway);
    for (const [key, val] of p) u.searchParams.set(key, val);
    if (e.id === 'knowledge') {
      u.hash = u.searchParams.toString();
      u.search = '';
    }
    return (isPublic ? u.pathname.slice(1) : u.pathname) + u.search + u.hash;
  }
  function remoteURL(origin, gatewayHref) {
    const host = new URL(origin);
    if (
      host.protocol !== 'https:' ||
      host.username ||
      host.password ||
      host.pathname !== '/' ||
      host.search ||
      host.hash
    )
      throw Error('접속 주소를 확인할 수 없습니다.');
    const source = new URL(gatewayHref, host),
      id = source.searchParams.get('destination') || 'context';
    if (!byId[id]?.path) throw Error('연결할 작업 공간을 확인해 주세요.');
    const target = new URL(href(id, false, context(source.href)), host);
    const dest =
      id === 'knowledge' ? new URLSearchParams(target.hash.slice(1)) : target.searchParams;
    for (const key of extraKeys[id] || []) {
      const value = source.searchParams.get(key);
      if (value && value.length <= 4096) dest.set(key, value);
    }
    if (id === 'knowledge') target.hash = dest.toString();
    return target.href;
  }
  function gateway(id, value = {}, sourceHref = '') {
    const u = new URL('context.html', 'https://workspace.invalid/');
    u.searchParams.set('destination', byId[id]?.path ? id : 'context');
    for (const [key, val] of parameters(id, value)) u.searchParams.set(key, val);
    if (sourceHref) {
      const source = new URL(sourceHref, u);
      const p =
        id === 'knowledge' && source.hash
          ? new URLSearchParams(source.hash.slice(1))
          : source.searchParams;
      for (const key of extraKeys[id] || []) if (p.has(key)) u.searchParams.set(key, p.get(key));
    }
    return 'context.html' + u.search;
  }
  function snapshotHref(id, value = {}, sourceHref = '') {
    const u = new URL(href(id, true, value), 'https://workspace.invalid/');
    // Only these projections share stable selections with their live view.
    // Public news/risk row hashes must never be used as live database IDs.
    if (sourceHref && ['wiki', 'knowledge', 'observatory', 'papers'].includes(id)) {
      const source = new URL(sourceHref, u);
      const p =
        id === 'knowledge' && source.hash
          ? new URLSearchParams(source.hash.slice(1))
          : source.searchParams;
      const target = id === 'knowledge' ? new URLSearchParams(u.hash.slice(1)) : u.searchParams;
      for (const key of extraKeys[id] || []) {
        if (p.has(key)) target.set(id === 'papers' && key === 'paper' ? 'id' : key, p.get(key));
      }
      if (id === 'knowledge') u.hash = target.toString();
    }
    return u.pathname.slice(1) + u.search + u.hash;
  }
  const api = {
    spaces,
    entries,
    byId,
    publicNames,
    publicURL,
    identify,
    context,
    parameters,
    href,
    remoteURL,
    gateway,
    snapshotHref,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.WorkspaceNavigation = api;
  if (typeof document === 'undefined') return;

  const isPublic = document.documentElement.dataset.public === 'true';
  const standalone =
    isPublic &&
    document.documentElement.dataset.mode === 'static' &&
    !document.documentElement.dataset.split;
  const current = standalone ? 'knowledge' : identify(location.href, isPublic);
  const entry = byId[current],
    space = spaces.find((s) => s.id === entry.space) || spaces[0];
  const make = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  };
  const paths = {
    pulse: 'M3 12h4l3-8 4 16 3-8h4',
    spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
    news: 'M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h3 M14 15h2 M8 18h8',
    paper: 'M6 3h8l4 4v14H6Z M14 3v5h4 M9 12h6 M9 16h6',
    network: 'M12 5 5 17h14Z M12 2v6 M2 17h6 M16 17h6',
    shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z M8 12l3 3 5-6',
    compass: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M16 8l-3 5-5 3 3-5Z',
    activity: 'M4 20V10 M10 20V4 M16 20v-7 M22 20H2',
    search: 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
    arrow: 'M5 12h14 M13 6l6 6-6 6',
  };
  function icon(name) {
    const n = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    n.setAttribute('viewBox', '0 0 24 24');
    n.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(n.namespaceURI, 'path');
    p.setAttribute('d', paths[name] || paths.spark);
    n.append(p);
    return n;
  }
  const carried = context(location.href);
  const querySelector =
    ':is(input, textarea):is(#query, #remote-query, #search, #q, #paper-search, #risk-search, #theme-search)';
  let queryEdited = false;
  function capture() {
    const value = context(location.href);
    for (const key of ['q', 'date', 'topic', 'window']) if (!value[key]) value[key] = carried[key];
    const input = document.querySelector(querySelector);
    if (
      input &&
      (queryEdited ||
        input.value ||
        !(carried.q.length > 256 && !['context', 'forecast'].includes(current)))
    )
      value.q = input.value;
    const day = document.querySelector('#date-filter, #day, #date');
    if (day) value.date = /^\d{4}-\d{2}-\d{2}$/.test(day.value) ? day.value : '';
    const topic = document.querySelector('#topic-filter, #topic');
    if (topic) value.topic = topic.value;
    return value;
  }
  function destinationLink(id, cls = '', label = '') {
    const e = byId[id],
      a = make('a', cls, label || e.label);
    a.dataset.destination = id;
    a.href = navigationHref(id, context(location.href));
    if (isPublic && !e.snapshot) {
      a.dataset.access = 'connected';
      a.title = '로그인 후 실시간 작업 공간에서 이어집니다.';
    }
    return a;
  }
  function navigationHref(id, value) {
    return standalone
      ? 'index.html#' + parameters('knowledge', value).toString()
      : href(id, isPublic, value);
  }
  const header = make('header', 'ws-header');
  const brandRow = make('div', 'ws-brand-row');
  const brand = destinationLink('strategy', 'ws-brand');
  brand.replaceChildren();
  brand.append(
    make('span', 'ws-brand-mark', 'N'),
    make('span', 'ws-brand-name', 'AI 뉴스'),
    make('span', 'ws-brand-sub', 'CONNECTED INTELLIGENCE')
  );
  const status = make('span', 'ws-capability', isPublic ? '공개 스냅샷' : '실시간 서버');
  status.dataset.mode = isPublic ? 'snapshot' : 'live';
  const searchButton = make('button', 'ws-command-trigger');
  searchButton.type = 'button';
  searchButton.append(icon('search'), make('span', '', '메뉴·기능 찾기'), make('kbd', '', '⌘ K'));
  searchButton.setAttribute('aria-label', '메뉴·기능 빠르게 찾기');
  const toggle = make('button', 'ws-menu-toggle', '작업 공간');
  toggle.type = 'button';
  toggle.setAttribute('aria-controls', 'ws-spaces-panel');
  const mode = make('a', 'ws-mode-link', isPublic ? '실시간으로 열기 ↗' : '공개 페이지로 ↗');
  mode.id = 'ws-mode-switch';
  const homePublic = 'https://hollobit.github.io/AI-News/';
  function modeURL() {
    if (isPublic) {
      // Public article hashes are not live row identities. Continue by query;
      // exact source links below individual cards carry their original URL.
      let sourceHref = location.href;
      if (current === 'papers') {
        const selected = new URL(sourceHref);
        if (selected.searchParams.has('id'))
          selected.searchParams.set('paper', selected.searchParams.get('id'));
        sourceHref = selected.href;
      }
      return gateway(
        current === 'services' ? 'context' : current,
        capture(),
        [
          'knowledge',
          'observatory',
          'wiki',
          'context',
          'forecast',
          'simulation',
          'graph',
          'archive',
          'papers',
        ].includes(current)
          ? sourceHref
          : ''
      );
    }
    return (
      homePublic +
      (entry.snapshot
        ? snapshotHref(current, capture(), location.href)
        : gateway(current, capture(), location.href))
    );
  }
  mode.href = modeURL();
  brandRow.append(brand, status, searchButton, mode, toggle);
  const nav = make('nav', 'ws-global-menu');
  nav.id = isPublic ? 'menu' : 'ws-global-menu';
  nav.setAttribute('aria-label', '주요 작업 공간');
  document.getElementById('menu')?.remove();
  for (const s of standalone ? spaces.filter((s) => s.id === 'knowledge') : spaces) {
    const a = destinationLink(standalone ? 'knowledge' : s.home, 'ws-space-link');
    a.dataset.space = s.id;
    a.dataset.route = s.home;
    a.replaceChildren(icon(s.icon), make('span', '', s.label));
    if (space.id === s.id) a.setAttribute('aria-current', 'page');
    nav.append(a);
  }
  const detail = make('div', 'ws-workspace-row');
  const subnav = make('nav', 'ws-subnav');
  subnav.setAttribute('aria-label', space.label + ' 세부 기능');
  for (const e of entries.filter((e) => e.space === space.id && e.id !== 'article')) {
    if (standalone && e.id !== 'knowledge') continue;
    const a = destinationLink(e.id);
    if (e.id === current) a.setAttribute('aria-current', 'page');
    if (isPublic && !e.snapshot) a.append(make('span', 'ws-access-mark', '↗'));
    subnav.append(a);
  }
  const stamp = make(
    'span',
    'ws-data-status',
    standalone
      ? '읽기 전용 지식 스냅샷'
      : isPublic
        ? '공개 자료 기준 시각 확인 중'
        : '자료 조회 · 분석 실행 · 검토 이력'
  );
  stamp.setAttribute('role', 'status');
  detail.append(subnav, stamp);
  const panel = make('section', 'ws-spaces-panel');
  panel.id = 'ws-spaces-panel';
  panel.hidden = true;
  panel.setAttribute('aria-label', '모든 작업 공간과 기능');
  panel.append(
    make(
      'p',
      'ws-panel-intro',
      standalone ? '검토된 지식의 연결을 탐색하세요.' : '하나의 흐름, 여덟 개의 작업 공간.'
    )
  );
  const grid = make('div', 'ws-spaces-grid');
  for (const s of standalone ? spaces.filter((s) => s.id === 'knowledge') : spaces) {
    const box = make('section', 'ws-space-card'),
      title = destinationLink(standalone ? 'knowledge' : s.home, 'ws-space-title', s.label);
    title.prepend(icon(s.icon));
    box.append(title, make('p', '', s.description));
    const links = make('div', 'ws-space-tools');
    for (const e of entries.filter(
      (e) => e.space === s.id && e.id !== 'article' && (!standalone || e.id === 'knowledge')
    ))
      links.append(destinationLink(e.id));
    box.append(links);
    grid.append(box);
  }
  panel.append(grid);
  if (!standalone) {
    const help = make('a', 'ws-help-link', '공개·실시간 이용 안내 ↗');
    help.href = (isPublic ? '' : homePublic) + 'index.html?view=services';
    panel.append(help);
  } else mode.hidden = true;
  header.append(brandRow, nav, detail, panel);
  let skip = document.querySelector('body > a:is(.skip, .workspace-skip)');
  if (!skip) {
    skip = make('a', 'workspace-skip', '본문으로 이동');
    const main = document.querySelector('main');
    if (main && !main.id) main.id = 'main';
    skip.href = '#' + (main?.id || 'main');
    document.body.prepend(skip);
  }
  skip.after(header);
  document.body.classList.add('ws-shell');
  document.body.dataset.workspace = space.id;
  const hint = make('div', 'ws-context-strip');
  hint.hidden = true;
  header.append(hint);
  function updateLinks() {
    const value = capture();
    for (const a of header.querySelectorAll('a[data-destination]'))
      a.href = navigationHref(a.dataset.destination, value);
    mode.href = modeURL();
    const q = value.q.trim();
    hint.hidden = !q;
    if (q) {
      const label = make(
        'span',
        'ws-context-label',
        q.length > 256 && !['context', 'forecast'].includes(current)
          ? '분석실에서 이어갈 질문'
          : '탐색 중'
      );
      const text = make('span', 'ws-context-query', q);
      text.title = q;
      const go = destinationLink(
        'context',
        'ws-context-action',
        standalone ? '지식 지도에서 찾기 →' : '이 질문으로 통합 탐색 →'
      );
      go.href = navigationHref('context', value);
      hint.replaceChildren(label, text, go);
    }
  }
  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  }
  setOpen(false);
  toggle.addEventListener('click', () => setOpen(panel.hidden));
  document.addEventListener('click', (e) => {
    if (!header.contains(e.target)) setOpen(false);
  });
  document.addEventListener('input', (e) => {
    if (e.target.matches(querySelector)) queryEdited = true;
    if (!e.target.closest('.ws-palette')) updateLinks();
  });
  document.addEventListener('change', updateLinks);
  addEventListener('popstate', updateLinks);
  addEventListener('hashchange', () => {
    if (!standalone && identify(location.href, isPublic) !== current) location.reload();
    else updateLinks();
  });
  header.addEventListener('click', (event) => {
    const a = event.target.closest('a[data-destination]');
    if (!a) return;
    a.href = navigationHref(a.dataset.destination, capture());
    try {
      const previous = JSON.parse(localStorage.getItem('news.workspace.recent') || '[]');
      localStorage.setItem(
        'news.workspace.recent',
        JSON.stringify(
          [
            a.dataset.destination,
            ...previous.filter((id) => id !== a.dataset.destination && byId[id]),
          ].slice(0, 6)
        )
      );
    } catch (_) {}
  });
  mode.addEventListener('click', () => {
    mode.href = modeURL();
  });
  new ResizeObserver(() =>
    document.documentElement.style.setProperty('--ws-header-height', header.offsetHeight + 'px')
  ).observe(header);

  const dialog = make('dialog', 'ws-palette');
  dialog.setAttribute('aria-labelledby', 'ws-palette-title');
  const dialogHead = make('div', 'ws-palette-head');
  const title = make('h2', '', '어디로 이어갈까요?');
  title.id = 'ws-palette-title';
  const close = make('button', 'ws-palette-close', '닫기');
  close.type = 'button';
  close.setAttribute('aria-label', '빠른 찾기 닫기');
  close.onclick = () => dialog.close();
  dialogHead.append(title, close);
  const input = make('input', 'ws-palette-input');
  input.type = 'search';
  input.placeholder = '기능 이름 또는 분석할 질문을 입력하세요';
  input.setAttribute('aria-label', '메뉴 또는 분석 질문');
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-controls', 'ws-command-results');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'true');
  input.maxLength = 2000;
  const list = make('div', 'ws-command-results');
  list.id = 'ws-command-results';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '이동할 기능');
  const summary = make('p', 'ws-palette-summary');
  summary.setAttribute('aria-live', 'polite');
  dialog.append(
    dialogHead,
    input,
    summary,
    list,
    make(
      'p',
      'ws-palette-help',
      '↑ ↓ 이동 · Enter 열기 · Esc 닫기 · 메뉴 이동은 분석을 새로 실행하지 않습니다.'
    )
  );
  document.body.append(dialog);
  let selected = 0,
    options = [];
  function select(index) {
    selected = Math.max(0, Math.min(options.length - 1, index));
    options.forEach((a, i) => a.setAttribute('aria-selected', String(i === selected)));
    if (options[selected]) {
      input.setAttribute('aria-activedescendant', options[selected].id);
      options[selected].scrollIntoView({ block: 'nearest' });
    }
  }
  function commands() {
    const raw = input.value.trim(),
      q = raw.normalize('NFKC').toLocaleLowerCase();
    let recent = [];
    try {
      recent = JSON.parse(localStorage.getItem('news.workspace.recent') || '[]').filter(
        (id) => byId[id]
      );
    } catch (_) {}
    let choices = entries.filter(
      (e) =>
        e.path &&
        e.id !== 'article' &&
        (!standalone || e.id === 'knowledge') &&
        (!q ||
          [e.label, e.description, spaces.find((s) => s.id === e.space)?.label]
            .join(' ')
            .normalize('NFKC')
            .toLocaleLowerCase()
            .includes(q))
    );
    if (!q)
      choices.sort((a, b) => {
        const score = (e) =>
          recent.includes(e.id) ? recent.indexOf(e.id) : 10 + (e.space === space.id ? 0 : 1);
        return score(a) - score(b);
      });
    else
      choices.sort(
        (a, b) =>
          Number(!a.label.toLocaleLowerCase().includes(q)) -
          Number(!b.label.toLocaleLowerCase().includes(q))
      );
    list.replaceChildren();
    options = [];
    function option(e, text, description, value) {
      const a = destinationLink(e.id, 'ws-command-option');
      a.href = navigationHref(e.id, value);
      a.id = 'ws-command-' + options.length;
      a.setAttribute('role', 'option');
      a.tabIndex = -1;
      a.replaceChildren(
        icon(spaces.find((s) => s.id === e.space)?.icon),
        make('span', 'ws-command-copy')
      );
      a.lastChild.append(make('strong', '', text), make('small', '', description));
      a.append(make('span', 'ws-command-arrow', '↗'));
      a.addEventListener('click', () => {
        try {
          localStorage.setItem(
            'news.workspace.recent',
            JSON.stringify([e.id, ...recent.filter((id) => id !== e.id)].slice(0, 6))
          );
        } catch (_) {}
      });
      list.append(a);
      options.push(a);
    }
    if (q)
      option(
        standalone ? byId.knowledge : byId.context,
        standalone ? '지식 지도에서 찾기' : '이 질문으로 통합 분석실 열기',
        raw,
        { ...capture(), q: raw }
      );
    for (const e of choices.slice(0, 18))
      option(
        e,
        e.label,
        (spaces.find((s) => s.id === e.space)?.label || '') +
          ' · ' +
          e.description +
          (isPublic && !e.snapshot ? ' · 로그인 후 이용' : ''),
        capture()
      );
    summary.textContent = q
      ? `연결 가능한 기능 ${choices.length}개`
      : recent.length
        ? '최근 사용한 기능과 현재 작업 공간'
        : '현재 작업 공간에서 시작해 보세요';
    select(0);
  }
  function openPalette() {
    setOpen(false);
    dialog.showModal();
    input.value = '';
    commands();
    input.focus();
  }
  searchButton.addEventListener('click', openPalette);
  dialog.addEventListener('close', () => searchButton.focus());
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
        dialog.close();
    }
  });
  input.addEventListener('input', commands);
  input.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      dialog.close();
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      select(selected + (e.key === 'ArrowDown' ? 1 : -1));
    }
    if (e.key === 'Enter' && options[selected]) {
      e.preventDefault();
      options[selected].click();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (dialog.open) dialog.close();
      else openPalette();
    } else if (e.key === 'Escape' && !panel.hidden) {
      setOpen(false);
      toggle.focus();
    }
  });
  if (current === 'article') {
    const back = destinationLink('news', 'ws-back', '← 뉴스·자료');
    document.querySelector('main')?.prepend(back);
  }
  if (['strategy', 'operations'].includes(current)) {
    const label = document.querySelector('.topbar>span');
    if (label) label.textContent = space.label + ' / ' + entry.label;
  }
  async function freshness() {
    if (!isPublic || standalone) return;
    try {
      let data;
      if (root.PublicData) data = await root.PublicData.manifest();
      else {
        const response = await fetch('site-manifest.json', {
          cache: 'no-store',
          signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) throw Error('metadata');
        data = await response.json();
      }
      const date = new Date(data.exported_at);
      if (Number.isNaN(date.getTime())) throw Error('date');
      stamp.textContent =
        '자료 기준 ' +
        date.toLocaleString('ko-KR', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          timeZone: 'Asia/Seoul',
        }) +
        ' KST';
      stamp.title = '게시 시점에 검토된 공개 자료입니다. 실시간 화면과 제공 범위가 다릅니다.';
      if (Date.now() - date.getTime() > 30 * 60 * 1000) {
        stamp.dataset.stale = 'true';
        stamp.textContent += ' · 최신 자료는 실시간에서 확인';
      }
    } catch (_) {
      stamp.textContent = '공개 자료 기준 시각 확인 불가';
      stamp.dataset.stale = 'true';
    }
  }
  function ready() {
    updateLinks();
    freshness();
  }
  if (document.readyState !== 'complete')
    document.addEventListener('DOMContentLoaded', ready, { once: true });
  else ready();
})(typeof window !== 'undefined' ? window : globalThis);
