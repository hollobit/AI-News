/* Canonical destinations and a shared global menu; local sections stay local. */
(() => {
  'use strict';
  const entries = [
    ['strategy', '전략 대시보드', '/strategy', '읽기·탐색'],
    ['news', '뉴스', '/news', '읽기·탐색'],
    ['archive', 'URL 보관함', '/archive', '읽기·탐색', '날짜별 아카이브'],
    ['observatory', '관측 지도', '/observatory', '읽기·탐색'],
    ['papers', '논문·연구동향', '/papers', '읽기·탐색', '논문'],
    ['wiki', '지식 위키', '/wiki', '읽기·탐색'],
    ['knowledge', '지식 관계 지도', '/knowledge', '읽기·탐색'],
    ['intelligence', '전략 검토실', '/intelligence', '분석·검토'],
    ['risks', '위험 평가', '/risks', '분석·검토', '위험·조건부 시나리오'],
    ['graph', '근거 질문·관계 분석', '/graph', '분석·검토', '관계 탐색'],
    ['research', '종합 분석', '/research', '분석·검토', '뉴스 분석'],
    ['simulation', '전략 시뮬레이션', '/simulation', '분석·검토', 'MiroFish 분석'],
    ['operations', '수집·분석 운영', '/operations', '운영'],
    ['sources', '외부 자료', '/sources', '운영', '출처 목록'],
    ['services', '서비스 안내', null, '운영'],
  ].map(([id, label, path, group, publicLabel]) => ({
    id,
    label,
    path,
    group,
    publicLabel: publicLabel || label,
  }));
  const publicOrder = [
    'strategy',
    'news',
    'archive',
    'observatory',
    'research',
    'risks',
    'papers',
    'wiki',
    'graph',
    'sources',
    'simulation',
    'services',
  ];
  const publicNames = Object.fromEntries(
    publicOrder.map((id) => [id, entries.find((e) => e.id === id).publicLabel])
  );
  const publicURL = (id) =>
    id === 'observatory'
      ? 'observatory.html'
      : id === 'graph'
        ? 'knowledge.html'
        : 'index.html?view=' + id;
  window.WorkspaceNavigation = { entries, publicNames, publicURL };
  const isPublic = document.documentElement.dataset.public === 'true';
  const standalone =
    isPublic &&
    document.documentElement.dataset.mode === 'static' &&
    !document.documentElement.dataset.split;
  const path = location.pathname;
  const isStrategy =
    ['/', '/strategy', '/operations'].includes(path) && !!document.querySelector('.sidebar');
  const current = standalone
    ? 'graph'
    : isPublic
      ? path.endsWith('knowledge.html')
        ? 'graph'
        : path.endsWith('observatory.html')
          ? 'observatory'
          : new URLSearchParams(location.search).get('view') || 'strategy'
      : isStrategy
        ? path === '/operations' ||
          ['#baseline', '#workflow', '#improvement'].includes(location.hash)
          ? 'operations'
          : 'strategy'
        : path === '/'
          ? 'news'
          : entries.find((e) => e.path && (path === e.path || path.startsWith(e.path + '/')))?.id;
  const make = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  };
  const header = make('header', 'ws-header'),
    row = make('div', 'ws-brand-row');
  const brand = make('a', 'ws-brand', 'AI 뉴스 · 전략 관측소');
  brand.href = isPublic ? 'index.html' : '/strategy';
  const context = make(
    'span',
    'ws-context',
    (isPublic ? publicNames[current] : entries.find((e) => e.id === current)?.label) || '기사 해설'
  );
  const toggle = make('button', 'ws-menu-toggle', '전체 메뉴');
  toggle.type = 'button';
  toggle.setAttribute('aria-controls', 'ws-global-menu');
  const menu = make('nav', 'ws-global-menu');
  menu.id = 'ws-global-menu';
  menu.setAttribute('aria-label', '전체 메뉴');
  const oldMenu = document.getElementById('menu');
  if (oldMenu) oldMenu.remove();
  if (isPublic) {
    menu.id = 'menu';
    toggle.setAttribute('aria-controls', 'menu');
  }
  const available = standalone
    ? [entries.find((e) => e.id === 'graph')]
    : isPublic
      ? publicOrder.map((id) => entries.find((e) => e.id === id))
      : entries.filter((e) => e.path);
  for (const group of ['읽기·탐색', '분석·검토', '운영']) {
    const section = make('div', 'ws-menu-group');
    section.append(make('span', 'ws-menu-caption', group));
    for (const item of available.filter((e) => e.group === group)) {
      const a = make('a', '', isPublic ? item.publicLabel : item.label);
      a.href = standalone ? 'index.html' : isPublic ? publicURL(item.id) : item.path;
      a.dataset.route = item.id;
      if (item.id === current) a.setAttribute('aria-current', 'page');
      section.append(a);
    }
    menu.append(section);
  }
  row.append(brand, context, toggle);
  if (isPublic) row.append(make('span', 'ws-capability', '공개 · 읽기 전용'));
  header.append(row, menu);
  document.body.prepend(header);
  document.body.classList.add('ws-shell');
  if (current === 'article' || path === '/article') {
    const back = make('a', 'ws-back', '← 뉴스 목록');
    back.href = '/news';
    document.querySelector('main').prepend(back);
  }
  const media = matchMedia('(max-width: 760px)');
  const setOpen = (open) => {
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  };
  setOpen(!media.matches);
  media.addEventListener('change', () => setOpen(!media.matches));
  toggle.addEventListener('click', () => setOpen(menu.hidden));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden && media.matches) {
      setOpen(false);
      toggle.focus();
    }
  });
  document.addEventListener('click', (e) => {
    if (media.matches && !header.contains(e.target)) setOpen(false);
  });
  new ResizeObserver(() =>
    document.documentElement.style.setProperty('--ws-header-height', header.offsetHeight + 'px')
  ).observe(header);
  if (isStrategy) {
    const h = document.querySelector('.topbar>span');
    if (h) h.textContent = current === 'operations' ? '운영 / 수집·분석' : '읽기 / 전략 대시보드';
  }
})();
