(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const node = (tag, cls = '', text = '') => {
    const n = document.createElement(tag);
    n.className = cls;
    n.textContent = text;
    return n;
  };
  const num = (n) => Number(n || 0).toLocaleString('ko-KR');
  const state = {
    data: null,
    lens: '',
    terms: '',
    keyword: '',
    morph: '',
    visible: 9,
    request: 0,
    selectedNodes: [],
    page: 1,
    overview: null,
    graph: null,
    newsLoading: false,
  };
  let watches = [];
  try {
    watches = JSON.parse(localStorage.getItem('ai-strategy-watchlist') || '[]');
    if (!Array.isArray(watches)) watches = [];
  } catch (_) {}
  function safe(url) {
    try {
      const u = new URL(url, location.origin);
      return ['http:', 'https:'].includes(u.protocol) ? u.href : '';
    } catch (_) {
      return '';
    }
  }
  function link(text, url, cls = 'text-link') {
    const a = node('a', cls, text);
    a.href = safe(url) || '#';
    if (a.href.startsWith('http') && !a.href.startsWith(location.origin + '/')) {
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
    }
    return a;
  }
  function notify(text, error = false) {
    $('#notice').hidden = false;
    $('#notice').className = 'notice' + (error ? ' error' : '');
    $('#notice').textContent = text;
  }
  const responseCache = new Map();
  const statusPoller = Workspace.poller((error) => notify(error.message, true));
  const api = (url, body, signal) => Workspace.request(url, { body, signal, cache: responseCache });
  const scheduleStatus = (name, fn, delay = 5000) => statusPoller.schedule(name, fn, delay);
  const stopStatus = (name) => statusPoller.stop(name);
  const retryStatus = (name, fn, error, delay = 5000) => statusPoller.retry(name, fn, error, delay);
  const operationsMode =
    location.pathname === '/operations' ||
    ['baseline', 'workflow', 'improvement'].includes(location.hash.slice(1));
  document.documentElement.dataset.workspace = operationsMode ? 'operations' : 'reading';
  const updateNavigation = () =>
    Workspace.currentNavigation(operationsMode ? '/operations' : '/strategy');
  updateNavigation();
  addEventListener('hashchange', updateNavigation);
  if (operationsMode) {
    document.title = '수집·분석 운영 · 하루 뉴스';
    $('#overview h1').textContent = '수집·분석 운영';
    $('#reading-actions').open = true;
    $('#overview p').textContent = '수집과 분석 대기열, 검토 상태, 중지·재개를 관리합니다.';
  }

  async function loadCorpusStatus() {
    try {
      const data = await api('/api/corpus/status');
      if (data.status === 'ready') {
        $('#live-corpus-count').textContent =
          `현재 수집 고유 뉴스 ${num(data.total_unique)}건 · 기본 분석 대상 ${num(data.baseline_total)}건 · 분석 대상에 새로 추가할 뉴스 ${num(data.new_since_baseline)}건${data.refreshing ? ' · 최신 수집량 갱신 중' : ''}`;
      } else $('#live-corpus-count').textContent = data.error || '현재 수집량 집계 중…';
      scheduleStatus('corpus-status', loadCorpusStatus, 10000);
    } catch (error) {
      $('#live-corpus-count').textContent = '현재 수집량 확인 재시도 중…';
      retryStatus('corpus-status', loadCorpusStatus, error, 10000);
    }
  }
  function channelTime(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return '기록 없음';
    return (
      new Intl.DateTimeFormat('ko-KR', {
        timeZone: 'Asia/Seoul',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date(value)) + ' KST'
    );
  }
  async function loadChannelStatus() {
    const area = $('#channel-read-times');
    try {
      const data = await api('/api/collector/channels', undefined, AbortSignal.timeout(5000));
      area.replaceChildren();
      for (const channel of data.channels || []) {
        const row = node('div', 'channel-status-row');
        row.append(
          node('strong', '', channel.name),
          node('span', '', '마지막 읽기 확인: ' + channelTime(channel.last_checked_at)),
          node('span', '', '메시지 수신: ' + channelTime(channel.last_received_at)),
          node('span', '', '메시지 저장: ' + channelTime(channel.last_saved_at)),
          node('span', '', '뉴스 추출 완료: ' + channelTime(channel.last_extracted_at))
        );
        if (!channel.last_received_at)
          row.append(
            node(
              'small',
              '',
              channel.messages
                ? '기존 뉴스가 있습니다. 실제 수신 시각은 기록 시작 이후 표시됩니다.'
                : '아직 수신된 뉴스가 없습니다.'
            )
          );
        area.append(row);
      }
      if (!area.childElementCount) area.append(node('span', '', '등록된 채널이 없습니다.'));
      if (data.pipeline) {
        const p = data.pipeline;
        if (p.status !== 'complete')
          area.append(node('p', '', '신규 메시지 확인 완료 → 고유 뉴스 추출·집계 중…'));
        else
          area.append(
            node(
              'p',
              '',
              `신규 메시지 확인 → 저장·뉴스 분리 → 중복 제거 완료: ${num(p.total_unique)}건${p.new_unique == null ? ' · 최초 집계' : ` · 직전 추출 대비 ${p.new_unique >= 0 ? '+' : ''}${num(p.new_unique)}건`} (${channelTime(p.extracted_at)})`
            )
          );
      }
      area.title = data.scope;
      scheduleStatus('channel-status', loadChannelStatus, 30000);
    } catch (error) {
      area.replaceChildren(
        node('span', '', '채널 수집 기록을 확인하지 못했습니다. 자동으로 다시 확인합니다.')
      );
      retryStatus('channel-status', loadChannelStatus, error, 30000);
    }
  }

  const route = Workspace.routeState(
    {
      q: 'search',
      date: 'date-filter',
      topic: 'topic-filter',
      sector: 'sector-filter',
      impact: 'impact-filter',
      sort: 'sort-filter',
    },
    { page: 1, lens: '', terms: '', keyword: '', strategic_keyword: '' }
  );
  let restoredPage = 1;
  function restoreRoute() {
    const saved = route.read();
    for (const key of ['lens', 'terms', 'keyword']) state[key] = saved[key];
    state.morph = saved.strategic_keyword;
    restoredPage = Math.max(1, Math.min(1000, Number(saved.page) || 1));
  }
  restoreRoute();
  addEventListener('popstate', () => {
    restoreRoute();
    load();
  });
  function query() {
    const p = new URLSearchParams({ date: $('#date-filter').value || 'all' });
    if ($('#search').value.trim()) p.set('q', $('#search').value.trim());
    if ($('#topic-filter').value) p.set('topic', $('#topic-filter').value);
    if ($('#sector-filter').value) p.set('sector', $('#sector-filter').value);
    if ($('#impact-filter').value) p.set('impact', $('#impact-filter').value);
    p.set('sort', $('#sort-filter').value);
    if (state.lens) p.set('lens', state.lens);
    if (state.terms) p.set('terms', state.terms);
    if (state.keyword) p.set('keyword', state.keyword);
    if (state.morph) p.set('strategic_keyword', state.morph);
    return p.toString();
  }
  async function action(button, work) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = '처리 중…';
    try {
      await work();
    } catch (e) {
      notify(e.message, true);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }
  function svgEl(tag, attrs = {}) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    return e;
  }
  function spark(values, color = '#4e9b7a') {
    const svg = svgEl('svg', {
      viewBox: '0 0 180 45',
      class: 'spark',
      'aria-label': '14일간 일별 문서 수: ' + values.join(', '),
      role: 'img',
    });
    const max = Math.max(...values, 1);
    const points = values.map((v, i) => [
      (i * 180) / Math.max(1, values.length - 1),
      40 - (v / max) * 32,
    ]);
    svg.append(
      svgEl('line', { x1: 90, y1: 0, x2: 90, y2: 45, stroke: '#dfe8df', 'stroke-dasharray': '3 3' })
    );
    svg.append(
      svgEl('polyline', {
        points: points.map((p) => p.join(',')).join(' '),
        fill: 'none',
        stroke: color,
        'stroke-width': '1.8',
        'stroke-linejoin': 'round',
      })
    );
    return svg;
  }
  function growth(item) {
    return Number(item.current) > 0 && Number(item.previous) === 0
      ? '신규 관측 · 이전 기간 비교 불가'
      : item.growth_pct == null
        ? '비교 자료 없음'
        : `${item.growth_pct > 0 ? '+' : ''}${item.growth_pct}%`;
  }
  function empty(parent, message, href, label) {
    const e = node('div', 'empty', message);
    if (href) {
      e.append(document.createElement('br'), link(label || '분석 열기 ↗', href, 'button'));
    }
    parent.append(e);
  }
  function renderMetrics(data) {
    const graph = data.graph?.summary || data.graph_summary || {};
    const metrics = [
      ['분석 범위 뉴스', num(data.total), '날짜·주제 필터 적용 · 중복 설명 정리'],
      ['보관한 URL', num(data.url_groups), '전체 수집 기록의 정규 URL 그룹'],
      [
        '원문 정보 확보',
        num(data.sources.counts.fetched),
        `${num(data.sources.total)}개 조회 · ${num(data.sources.pending)}개 대기`,
      ],
      [
        '의미 관계',
        graph.edge_count == null ? '조회 대기' : num(graph.edge_count),
        graph.node_count == null
          ? '관계 지도를 열면 집계를 표시합니다.'
          : `${num(graph.node_count)}개 개체 · 완료 분석 기준`,
      ],
    ];
    $('#metrics').replaceChildren(
      ...metrics.map(([title, value, note]) => {
        const m = node('div', 'metric');
        m.append(
          node('div', 'metric-title', title),
          node('div', 'metric-value', value),
          node('div', 'metric-note', note)
        );
        return m;
      })
    );
    const last = data.collector.last_success;
    const age = last ? (Date.now() - Date.parse(last)) / 1000 : Infinity;
    $('#collector-state').textContent = age < 100 ? 'Telegram 수집 연결됨' : '수집 연결 확인 필요';
    $('#collector-dot').style.background = age < 100 ? '#8fe2b9' : '#d9ab6f';
    $('#last-message').textContent =
      '최근 메시지 게시 ' + (data.collector.last_message || '없음').replace('T', ' ').slice(0, 16);
  }
  function updateLensSelection() {
    document.querySelectorAll('.lens-card, #lens-tabs .tab').forEach((button) => {
      const selected = button.dataset.lens
        ? button.dataset.lens === state.lens
        : !state.lens && !state.terms && !state.keyword && !state.morph;
      button.classList.toggle(
        button.classList.contains('lens-card') ? 'selected' : 'active',
        selected
      );
      button.setAttribute('aria-pressed', String(selected));
    });
  }
  function selectLens(id, scroll = true) {
    state.lens = id;
    state.terms = '';
    state.keyword = '';
    state.morph = '';
    state.visible = 9;
    load();
    if (scroll) $('#intelligence').scrollIntoView({ behavior: 'smooth' });
  }
  function renderLensTabs(lenses = []) {
    const tabs = $('#lens-tabs'),
      focused = document.activeElement;
    const existing = new Map([...tabs.children].map((button) => [button.dataset.lens, button]));
    const rows = [{ id: '', name: '전체 전략 주제' }, ...lenses];
    const ids = new Set(rows.map((lens) => lens.id));
    rows.forEach((lens, index) => {
      let button = existing.get(lens.id);
      if (!button) {
        button = node('button', 'tab');
        button.type = 'button';
        button.dataset.lens = lens.id;
        button.addEventListener('click', () => selectLens(lens.id, false));
      }
      if (button.textContent !== lens.name) button.textContent = lens.name;
      if (!lens.id) button.title = '전략 주제 선택을 해제합니다. 검색·날짜·분야 조건은 유지됩니다.';
      if (tabs.children[index] !== button) tabs.insertBefore(button, tabs.children[index] || null);
    });
    for (const [id, button] of existing) {
      if (!ids.has(id)) button.remove();
    }
    updateLensSelection();
    if (
      focused?.classList.contains('tab') &&
      existing.get(focused.dataset.lens) === focused &&
      !focused.isConnected
    ) {
      tabs.firstElementChild.focus({ preventScroll: true });
    }
  }
  function selectedScope() {
    const trends = state.overview?.trends || {};
    const lens = [
      ...(trends.lenses || []),
      ...(trends.monitoring?.topics || []),
      ...(trends.monitoring?.candidates || []),
    ].find((item) => item.id === state.lens);
    return (
      lens?.label ||
      lens?.name ||
      (state.lens
        ? '선택한 전략 주제'
        : state.terms
          ? '관심 검색어'
          : state.keyword || state.morph
            ? '선택 키워드'
            : '전체 전략 주제')
    );
  }
  function highlightTopic(element, item) {
    if (!state.lens) return element;
    const trends = state.overview?.trends || {};
    const lens = [
      ...(trends.lenses || []),
      ...(trends.monitoring?.topics || []),
      ...(trends.monitoring?.candidates || []),
    ].find((row) => row.id === state.lens);
    const terms = [
      ...new Set(
        [...(item.matched_terms || []), ...(lens?.terms || [])]
          .filter((t) => typeof t === 'string' && t.trim())
          .map((t) => t.trim())
      ),
    ].sort((a, b) => b.length - a.length);
    if (!terms.length) return element;
    const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(
      terms
        .map((term) =>
          /^[\x00-\x7f]+$/.test(term) ? `(?<![a-z0-9])${escape(term)}(?![a-z0-9])` : escape(term)
        )
        .join('|'),
      'giu'
    );
    const text = element.textContent,
      fragment = document.createDocumentFragment();
    let end = 0;
    for (const match of text.matchAll(pattern)) {
      fragment.append(
        document.createTextNode(text.slice(end, match.index)),
        node('mark', 'topic-highlight', match[0])
      );
      end = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(end)));
    element.replaceChildren(fragment);
    return element;
  }
  let showAllTopics = false;
  function observedLenses(lenses) {
    return lenses.filter((l) => Number(l.current) > 0);
  }
  function visibleLenses(lenses) {
    lenses = observedLenses(lenses);
    if (showAllTopics) return lenses;
    let auto = 0;
    return lenses.filter(
      (l) =>
        !l.dynamic ||
        l.origin === 'builtin' ||
        l.origin === 'manual' ||
        ++auto <= 12 ||
        l.id === state.lens
    );
  }
  function renderTrends(data) {
    const t = data.trends;
    if (
      t.lenses.some((l) => l.id === state.lens) &&
      !observedLenses(t.lenses).some((l) => l.id === state.lens)
    ) {
      state.lens = '';
      state.visible = 9;
      load();
    }
    const visible = visibleLenses(t.lenses);
    $('#topics-more').hidden =
      observedLenses(t.lenses).filter(
        (l) => l.dynamic && l.origin !== 'builtin' && l.origin !== 'manual'
      ).length <= 12;
    $('#topics-more').textContent = showAllTopics ? '자동 주제 접기' : '자동 주제 모두 보기';
    $('#period').textContent = `${t.start} — ${t.end} 관측`;
    $('#method').textContent =
      t.method +
      ' ' +
      t.weighting +
      ` 최근 ${num(t.current_documents)}개 / 직전 ${num(t.previous_documents)}개 문서.`;
    $('#lens-cards').replaceChildren(
      ...visible.map((lens) => {
        const b = node('button', 'lens-card' + (state.lens === lens.id ? ' selected' : ''));
        b.type = 'button';
        b.dataset.lens = lens.id;
        const top = node('div', 'lens-top', lens.name);
        top.append(node('span', '', '↗'));
        const ns = node('div', 'lens-numbers');
        ns.append(
          node('strong', '', num(lens.current)),
          node('span', 'delta' + (lens.growth_pct < 0 ? ' down' : ''), growth(lens))
        );
        b.append(
          top,
          node('div', 'lens-sub', lens.subtitle || lens.description || ''),
          ns,
          spark(lens.series),
          node(
            'div',
            'lens-base',
            `직전 ${num(lens.previous)}건 · 비중 ${lens.share_change_pp > 0 ? '+' : ''}${lens.share_change_pp}pp`
          )
        );
        if (lens.dynamic) {
          b.append(
            topics.badges(lens),
            node(
              'p',
              'dynamic-note',
              lens.verification_label || '근거 표현·규칙 검토 · 인과 확정 아님'
            )
          );
        }
        b.title = `검색어: ${lens.terms.join(', ')}${lens.low_sample ? ' · 작은 표본, 해석 주의' : ''}`;
        b.addEventListener('click', () => selectLens(lens.id));
        return b;
      })
    );
    renderLensTabs(visible);
    const area = $('#emerging');
    area.replaceChildren();
    t.emerging.slice(0, 6).forEach((term) => {
      const b = node('button', 'emerging-row');
      const title = node('div', 'emerging-name', term.label);
      title.append(
        node(
          'small',
          '',
          `${term.kind === 'technical_dictionary' ? '기술용어' : term.kind === 'noun_phrase' ? '복합 명사구' : '형태소 명사'} · 국가 ${term.country_documents} / 정책 ${term.government_documents} / 고영향 ${term.high_impact_documents || 0}건 · ×${term.strategic_multiplier}`
        )
      );
      const value = node('div', 'emerging-value', `+${term.share_change_pp}pp`);
      value.append(node('small', '', `${term.previous} → ${term.current}건`));
      b.title = `원문 표현: ${term.surface} · 전략 점수 ${term.strategic_score} · 분야: ${(term.impact_domains || []).map((d) => d.label + ' ' + d.documents + '건').join(', ') || '명시 단서 없음'} · ${term.basis}`;
      b.append(title, spark(term.series), value);
      b.addEventListener('click', () => {
        state.morph = term.id;
        state.keyword = '';
        state.lens = '';
        state.terms = '';
        $('#search').value = '';
        state.visible = 9;
        load();
        $('#intelligence').scrollIntoView({ behavior: 'smooth' });
      });
      area.append(b);
    });
    if (!t.emerging.length)
      empty(area, '두 기간을 비교할 만큼 반복 등장한 신규 후보가 아직 없습니다.');
  }

  function valueSummary(value, detailed = false) {
    const box = node('div', 'strategic-value' + (detailed ? ' detailed' : ''));
    box.append(node('span', 'pill green', `검토 우선점수 ${num(value.score)}`));
    const domains = node('div', 'impact-tags');
    (value.domains || []).forEach((d) => domains.append(node('span', 'pill', d.label || d.id)));
    box.append(domains);
    if (detailed) {
      box.append(
        node(
          'p',
          'subtle',
          '뉴스의 분야·국가·정책 언급에 따른 규칙 점수입니다. 사실일 확률이나 검증 완료도를 나타내지 않습니다.'
        )
      );
      const explanation = Array.isArray(value.explanation)
        ? value.explanation
        : [value.explanation];
      explanation.filter(Boolean).forEach((text) => box.append(node('p', '', String(text))));
      (value.domains || []).forEach((d) =>
        box.append(
          node(
            'p',
            '',
            `${d.label || d.id} · 가중치 ${d.weight} · 연결 표현: ${(d.matched_terms || []).join(', ') || '없음'}`
          )
        )
      );
      box.append(
        node(
          'p',
          'subtle',
          `국가 단서: ${(value.country_terms || []).join(', ') || '없음'} / 정부·정책 단서: ${(value.government_terms || []).join(', ') || '없음'}`
        )
      );
    } else box.title = '규칙 기반 검토 순위 · 제목을 눌러 점수 근거 확인';
    return box;
  }
  function renderImpacts(data) {
    const selected = $('#impact-filter').value;
    if (Array.isArray(data.impact_domains) && data.impact_domains.length) {
      $('#impact-filter').replaceChildren(
        new Option('모든 영향 분야', ''),
        ...data.impact_domains.map(
          (d) => new Option(`${d.label || d.name || d.id} (${num(d.count)})`, d.id)
        )
      );
      $('#impact-filter').value = selected;
    }
  }
  let monitoringData = null,
    showAllSignals = false,
    showAllCandidates = false;
  function signalTrend(signal) {
    const box = node('div', 'signal-trend');
    const current = Number(signal.current || 0),
      previous = Number(signal.previous || 0),
      delta = current - previous;
    const change =
      previous > 0
        ? `${delta > 0 ? '+' : ''}${num(delta)}건 (${delta > 0 ? '+' : ''}${Math.round((delta / previous) * 100)}%)`
        : current > 0
          ? `+${num(current)}건 · 이전 0건으로 증감률 계산 불가`
          : '두 기간 모두 0건';
    box.append(node('p', 'subtle', `직전 7일 대비 ${change}`));
    if (signal.series?.length) {
      box.append(spark(signal.series));
      const days = signal.days || [];
      if (days.length)
        box.append(
          node('small', 'subtle', `${days[0]} — ${days[days.length - 1]} · 일별 고유 문서`)
        );
      const detail = node('details', 'signal-daily');
      detail.append(node('summary', '', '일별 건수 확인'));
      const table = node('table', '');
      const head = node('tr', '');
      head.append(node('th', '', '날짜'), node('th', '', '고유 문서'));
      table.append(head);
      signal.series.forEach((count, i) => {
        const row = node('tr', '');
        row.append(node('td', '', days[i] || `${i + 1}일차`), node('td', '', `${num(count)}건`));
        table.append(row);
      });
      detail.append(table);
      box.append(detail);
    }
    return box;
  }
  function renderMonitoring(monitoring) {
    monitoringData = monitoring;
    const signals = $('#monitoring-signals'),
      candidates = $('#monitoring-candidates');
    signals.replaceChildren();
    candidates.replaceChildren();
    if (!monitoring) {
      empty(signals, '관측 지표를 불러오지 못했습니다. 새로고침해 주세요.');
      return;
    }
    if (monitoring.method) $('#monitoring-method').textContent = monitoring.method;
    $('#monitoring-sources').replaceChildren(
      ...(monitoring.sources || []).map((s) => link(s.title, s.url))
    );
    function card(signal, candidate = false) {
      const el = node('article', candidate ? 'monitoring-candidate' : 'monitoring-signal');
      if (signal.grouped) el.append(node('span', 'pill', '통합 관측 신호'));
      else if (signal.dynamic) el.append(topics.badges(signal));
      else if (!candidate) el.append(node('span', 'pill', '통제 주제 언급'));
      el.append(
        node('h4', '', signal.label || signal.name),
        node(
          'span',
          'pill',
          `${signal.current != null ? '최근 7일' : '14일 공동출현'} ${num(signal.current ?? signal.count ?? signal.documents)}건${signal.previous != null ? ' · 직전 7일 ' + num(signal.previous) + '건' : ''}`
        )
      );
      if (signal.observation_types) {
        const labels = {
          proposed: '제안 언급',
          negated_or_conditional: '부정·조건부',
          reported_mention: '변화 관련 언급',
        };
        el.append(
          node(
            'p',
            'subtle',
            Object.entries(labels)
              .filter(([key]) => Number(signal.observation_types[key]) > 0)
              .map(([key, label]) => `${label} ${num(signal.observation_types[key])}건`)
              .join(' · ')
          ),
          node('p', 'subtle', '표현의 관측 건수이며 실제 통제 시행을 확인한 수치가 아닙니다.')
        );
      }
      if (signal.basis || signal.description)
        el.append(node('p', 'subtle', signal.basis || signal.description));
      if (!candidate && signal.series?.length) el.append(signalTrend(signal));
      if (signal.grouped && signal.members?.length) {
        const members = node('details', 'signal-members');
        members.append(
          node('summary', '', `묶인 항목과 개별 추이 ${num(signal.members.length)}개`)
        );
        signal.members.forEach((member) => {
          const section = node('section', '');
          section.append(
            node('strong', '', member.label || member.name),
            node(
              'p',
              'subtle',
              `${member.origin === 'builtin' ? '고정 주제' : member.origin === 'manual' ? '직접 등록' : '자동 발견'} · 최근 7일 ${num(member.current)}건 · 직전 7일 ${num(member.previous)}건`
            ),
            signalTrend(member)
          );
          members.append(section);
        });
        el.append(members);
      }
      const evidence = signal.evidence || [];
      if (evidence.length) {
        const detail = node('details', 'monitoring-evidence');
        detail.append(node('summary', '', '관측 근거 확인'));
        evidence.slice(0, 3).forEach((e) => {
          detail.append(
            link(e.title || e.url || '수집 근거', e.url || e.source_url || '/news?date=all')
          );
          const quote = e.source_quotes || e.source_quote || e.quotes || e.quote;
          if (quote) detail.append(node('p', 'subtle', improvementText(quote)));
          const originLabels = {
            telegram_title: 'Telegram 제목',
            telegram_excerpt: 'Telegram 메시지 발췌',
            title: 'Telegram 제목',
            text: 'Telegram 메시지 발췌',
            excerpt: 'Telegram 메시지 발췌',
            fetched_url_title: '확보 원문 제목',
            fetched_url_excerpt: '확보 원문 발췌',
            fetched_title: '확보 원문 제목',
            fetched_text: '확보 원문 발췌',
          };
          if (e.day || e.origin)
            detail.append(
              node(
                'small',
                'subtle',
                [e.day, originLabels[e.origin] || e.origin].filter(Boolean).join(' · ')
              )
            );
          if (e.caution) detail.append(node('p', 'subtle', e.caution));
        });
        el.append(detail);
      } else el.append(node('p', 'subtle', '현재 관측 기간에 해당하는 근거가 없습니다.'));
      if (
        signal.id &&
        (!candidate || signal.match_mode === 'all' || signal.id.startsWith('cooccurrence:'))
      ) {
        const view = node('button', 'button', candidate ? '관련 뉴스 보기' : '이 신호의 뉴스 보기');
        view.addEventListener('click', () => selectLens(signal.id));
        el.append(view);
      }
      if (signal.dynamic)
        el.append(
          node(
            'p',
            'dynamic-note',
            signal.verification_label || '근거 표현·규칙 검토 · 인과 확정 아님'
          )
        );
      const terms = signal.terms || [signal.label || signal.name];
      const save = node('button', 'button', '＋ 관심 목록에 저장');
      save.addEventListener('click', () => {
        if (
          signal.id &&
          (!candidate || signal.match_mode === 'all' || signal.id.startsWith('cooccurrence:'))
        ) {
          if (!watches.some((w) => w.lens === signal.id)) {
            watches.push({ name: signal.label || signal.name, lens: signal.id, terms: '' });
            watches = watches.slice(-20);
            saveWatches();
          }
          notify(
            '관심 목록에 저장했습니다. 해당 관측 주제의 정확한 판별 기준으로 뉴스를 찾습니다.'
          );
        } else openWatch(signal.label || signal.name, terms.join(', '));
      });
      el.append(save);
      return el;
    }
    const signalItems = (monitoring.topics || monitoring.signals || []).filter(
      (s) => Number(s.current ?? s.count) > 0 && (s.evidence || []).length > 0
    );
    signalItems.slice(0, showAllSignals ? undefined : 4).forEach((s) => signals.append(card(s)));
    $('#signals-more').hidden = signalItems.length <= 4;
    $('#signals-more').textContent = showAllSignals ? '변화 신호 접기' : '변화 신호 더 보기';
    const related =
      monitoring.candidates ||
      (monitoring.topics || []).flatMap((topic) =>
        (topic.related_keywords || []).map((term) => ({
          ...term,
          label: `${topic.label || topic.name} · ${term.label}`,
          terms: [term.label],
          basis: `${topic.label || topic.name} 관련 문서에서 공동 출현 · ${term.topic_share_pct ?? 0}%`,
        }))
      );
    const supportedRelated = related.filter(
      (s) => Number(s.current ?? s.count ?? s.documents) > 0 && (s.evidence || []).length > 0
    );
    supportedRelated
      .slice(0, showAllCandidates ? undefined : 4)
      .forEach((s) => candidates.append(card(s, true)));
    $('#candidates-more').hidden = supportedRelated.length <= 4;
    $('#candidates-more').textContent = showAllCandidates ? '전략 후보 접기' : '전략 후보 더 보기';
    if (!signals.childElementCount)
      empty(signals, '현재 기간에 원문 근거가 확인된 AI 통제 신호가 없습니다.');
    if (!candidates.childElementCount)
      empty(candidates, '현재 관측 기간에서 근거가 충분한 공동 출현 후보가 없습니다.');
  }
  function newsCard(item) {
    const card = node('article', 'news-card');
    const meta = node('div', 'news-meta');
    meta.append(node('span', 'pill', item.topic_title || item.topic), node('time', '', item.day));
    const title = highlightTopic(node('button', 'news-title', item.title), item);
    title.addEventListener('click', () => openStory(item));
    const source = item.source_context || {};
    const analysis = item.strategic_analysis;
    const tags = node('div', 'news-tags');
    (item.strategic_keywords || []).slice(0, 4).forEach((k) => {
      const b = node('button', '', k.label);
      b.addEventListener('click', () => {
        state.morph = k.id;
        state.keyword = '';
        state.lens = '';
        state.terms = '';
        $('#search').value = '';
        state.visible = 9;
        load();
      });
      tags.append(b);
    });
    let host = '메시지 근거';
    try {
      host = new URL(item.source_url).hostname.replace(/^www\./, '');
    } catch (_) {}
    const foot = node('div', 'news-foot');
    const inspect = node(
      'button',
      '',
      analysis
        ? '전략 분석 보기 ↗'
        : source.status === 'fetched'
          ? '원문 발췌 보기 ↗'
          : '근거 살펴보기 ↗'
    );
    inspect.addEventListener('click', () => openStory(item));
    foot.append(node('span', '', host), inspect);
    if (item.source_url)
      foot.append(link('상세 해설 ↗', '/article?url=' + encodeURIComponent(item.source_url)));
    card.append(meta, title);
    if (analysis?.freshness === 'historical_unchecked')
      card.append(node('small', 'subtle', '저장된 연구 해석 · 현재 원문 일치 미확인'));
    if (!analysis && item.base_analysis)
      card.append(
        node(
          'span',
          'pill ' + (item.base_analysis.verified ? 'green' : 'amber'),
          item.base_analysis.verified ? '기본 분석 검토 통과' : '기본 분석 · 검토 필요'
        )
      );
    if (item.sectors?.length) {
      const sectors = node('div', 'impact-tags');
      item.sectors.forEach((s) => sectors.append(node('span', 'pill', s.label)));
      card.append(sectors);
    }
    if (item.strategic_value) card.append(valueSummary(item.strategic_value));
    card.append(
      highlightTopic(
        node(
          'p',
          '',
          analysis?.summary ||
            item.base_analysis?.summary ||
            item.base_analysis?.result?.summary ||
            item.excerpt ||
            item.text ||
            ''
        ),
        item
      ),
      tags,
      foot
    );
    return card;
  }
  function renderNewsNetwork(data) {
    const target = $('#strategy-news-network');
    if (!target || !window.NewsNetwork) return;
    const items = data.items || [];
    const center =
      items.length && items.every((item) => item.day === items[0].day)
        ? items[0].day
        : '최근 뉴스와 검토 분석';
    window.NewsNetwork.render(target, items, { centerLabel: center, limit: 48 });
  }
  function renderNews(data) {
    if (state.overview) renderMetrics({ ...state.overview, total: data.total, graph: state.graph });
    updateLensSelection();
    $('#news-count').textContent = num(data.total);
    const trends = state.overview?.trends || data.trends || {};
    const lens = [
      ...(trends.lenses || []),
      ...(trends.monitoring?.topics || []),
      ...(trends.monitoring?.candidates || []),
    ].find((l) => l.id === state.lens);
    $('#scope-label').textContent =
      `${lens ? (lens.label || lens.name) + ' · ' : ''}${state.terms ? '관심 검색어 ' + state.terms + ' · ' : ''}${state.keyword || state.morph ? '선택 키워드 · ' : ''}${data.unique_documents != null ? '전체 선택 기간 고유 근거 ' + num(data.unique_documents) + '개 · 게시 항목 ' + num(data.total) + '개' : num(data.total) + '개'} 중 ${$('#sort-filter').value === 'latest' ? '최신순' : '전략적 검토 우선순'} 상위 ${data.items.length}개 표시. 성장 지표는 전체 수집 기록 기준입니다. 검토 우선점수는 규칙 기반이며 사실일 확률을 뜻하지 않습니다.`;
    $('#news-grid').replaceChildren(...data.items.map(newsCard));
    renderNewsNetwork(data);
    if (!data.items.length)
      empty($('#news-grid'), '현재 조건에 맞는 뉴스가 없습니다. 검색어나 전략 주제를 바꿔보세요.');
    $('#more-news').hidden = data.items.length >= data.total;
  }
  function renderGraph(data) {
    const svg = $('#graph');
    svg.replaceChildren();
    const g = data.graph;
    const nodes = (g.nodes || []).slice(0, 16);
    const byId = new Map();
    if (!nodes.length) {
      const text = svgEl('text', {
        x: 340,
        y: 180,
        'text-anchor': 'middle',
        fill: '#8d9c91',
        'font-size': 13,
      });
      text.textContent = '관계 분석을 실행하면 근거 지도에 연결됩니다.';
      svg.append(text);
      return;
    }
    nodes.forEach((n, i) => {
      const ring = i < 5 ? 90 : 145;
      const angle =
        (i < 5 ? i / 5 : (i - 5) / Math.max(1, nodes.length - 5)) * Math.PI * 2 - Math.PI / 2;
      byId.set(n.id, {
        ...n,
        x: 340 + Math.cos(angle) * ring * 1.8,
        y: 180 + Math.sin(angle) * ring,
      });
    });
    const defs = svgEl('defs');
    const marker = svgEl('marker', {
      id: 'arrow',
      viewBox: '0 0 8 8',
      refX: 8,
      refY: 4,
      markerWidth: 5,
      markerHeight: 5,
      orient: 'auto-start-reverse',
    });
    marker.append(svgEl('path', { d: 'M0 0 L8 4 L0 8Z', fill: '#a9c1af' }));
    defs.append(marker);
    svg.append(defs);
    (g.edges || []).forEach((e) => {
      const a = byId.get(e.source),
        b = byId.get(e.target);
      if (!a || !b) return;
      const line = svgEl('line', {
        x1: a.x,
        y1: a.y,
        x2: b.x,
        y2: b.y,
        stroke: '#b6cbb9',
        'stroke-width': 1.2,
        'stroke-dasharray': e.confidence === 'inferred' ? '4 4' : '',
        'marker-end': 'url(#arrow)',
      });
      const title = svgEl('title');
      title.textContent = `${a.name} → ${b.name}: ${e.meaning || e.relation}`;
      line.append(title);
      svg.append(line);
    });
    byId.forEach((n) => {
      const isTech = ['Technology', 'Concept', 'Product', 'Paper'].includes(n.type);
      const group = svgEl('g', {
        tabindex: 0,
        role: 'button',
        'aria-label': n.name,
        style: 'cursor:pointer',
      });
      group.append(
        svgEl('circle', {
          cx: n.x,
          cy: n.y,
          r: 10 + Math.min(7, n.support_count || 0),
          fill: isTech ? '#c4dece' : '#3e7663',
          stroke: '#fff',
          'stroke-width': 3,
        })
      );
      const label = svgEl('text', {
        x: n.x,
        y: n.y + 30,
        'text-anchor': 'middle',
        'font-size': 11,
        fill: '#405d4c',
      });
      label.textContent = n.name.length > 17 ? n.name.slice(0, 16) + '…' : n.name;
      group.append(label);
      const select = async () => {
        state.selectedNodes = [n.id];
        $('#graph-detail').replaceChildren(
          node('strong', '', n.name + ' · ' + n.type),
          node('p', '', n.summary || '저장된 설명 없음')
        );
        try {
          const detail = await api(
            '/api/graph/integrated?node_ids=' +
              encodeURIComponent(n.id) +
              '&max_nodes=24&evidence_limit=12'
          );
          if (state.selectedNodes[0] !== n.id) return;
          (detail.evidence || [])
            .slice(0, 12)
            .forEach((e) =>
              $('#graph-detail').append(link(e.title || '연결 근거', e.source_url || '/graph'))
            );
        } catch (e) {
          $('#graph-detail').append(node('p', 'subtle', e.message));
        }
      };
      group.addEventListener('click', select);
      group.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          select();
        }
      });
      svg.append(group);
    });
  }
  function renderOutlook(data) {
    const el = $('#outlooks');
    el.replaceChildren();
    const latest = data.runs.find((r) => r.report);
    if (!latest) {
      const pending = data.runs.find((r) =>
        ['queued', 'running', 'paused', 'failed'].includes(r.status)
      );
      empty(
        el,
        pending
          ? `분석 ${pending.status} · ${num(pending.processed_documents)}/${num(pending.total_documents)}개 문서. ${pending.error || '완료된 보고서에서 전략 해석을 표시합니다.'}`
          : '아직 완료된 전략 보고서가 없습니다. 현재 범위의 주요 뉴스 24건을 분석하면 국가·기업 전략과 조건부 전망을 확인할 수 있습니다.',
        '/research',
        '보고서와 처리 상태 보기 ↗'
      );
      return;
    }
    const report = latest.report;
    el.append(node('p', '', report.summary));
    const labels = { short: '단기 · 0–3개월', medium: '중기 · 3–12개월', long: '장기 · 1–3년' };
    Object.entries(labels).forEach(([key, label]) => {
      const o = report.outlooks?.[key]?.[0];
      if (!o) return;
      const block = node('div', 'outlook-item');
      block.append(
        node('span', 'pill', label),
        node('h4', '', o.scenario),
        node('p', '', o.assessment),
        node('p', 'subtle', '확인할 신호: ' + (o.signals || []).join(' · ')),
        link('가정·위험·근거 확인 ↗', `/research?run=${encodeURIComponent(latest.id)}`)
      );
      el.append(block);
    });
    if (report.major_topics?.length) {
      const b = node('div', 'outlook-item');
      b.append(node('h4', '', '분석에서 도출된 전략 주제'));
      report.major_topics.slice(0, 5).forEach((t) => {
        const button = node('button', 'tab', t.name + ' ＋');
        button.title = t.meaning;
        button.addEventListener('click', () => openWatch(t.name, t.name));
        b.append(button);
      });
      el.append(b);
    }
  }
  let newsAbort;
  async function load(append = false) {
    const id = ++state.request;
    clearTimeout(searchTimer);
    if (!append) {
      state.page = restoredPage;
      restoredPage = 1;
    }
    route.write({
      page: state.page,
      lens: state.lens,
      terms: state.terms,
      keyword: state.keyword,
      strategic_keyword: state.morph,
    });
    newsAbort?.abort();
    const controller = new AbortController();
    newsAbort = controller;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 30000);
    state.newsLoading = true;
    updateLensSelection();
    $('#refresh').disabled = true;
    $('#more-news').disabled = true;
    $('#news-grid').setAttribute('aria-busy', 'true');
    $('#scope-label').textContent = selectedScope() + ' · 뉴스 근거를 불러오는 중…';
    if (!append) {
      $('#news-count').textContent = '…';
      $('#news-grid').replaceChildren(
        node('div', 'loading', '선택한 조건의 뉴스 근거를 불러오는 중…')
      );
      $('#more-news').hidden = true;
    }
    try {
      const data = await api(
        '/api/strategy?view=news&page=' + state.page + '&page_size=12&' + query(),
        undefined,
        controller.signal
      );
      if (id !== state.request) return;
      if (!append && state.page > 1) {
        const items = [];
        for (let n = 1; n < state.page; n++) {
          const part = await api(
            '/api/strategy?view=news&page=' + n + '&page_size=12&' + query(),
            undefined,
            controller.signal
          );
          if (id !== state.request) return;
          items.push(...part.items);
        }
        data.items = [...items, ...data.items];
      }
      if (append && state.data) data.items = [...state.data.items, ...data.items];
      state.data = data;
      const dateValue = $('#date-filter').value,
        topicValue = $('#topic-filter').value;
      if (data.dates) {
        $('#date-filter').replaceChildren(
          new Option('모든 날짜', 'all'),
          ...data.dates.map((d) => new Option(`${d.date} (${num(d.count)})`, d.date))
        );
        $('#date-filter').value = dateValue;
      }
      if (data.topics) {
        $('#topic-filter').replaceChildren(
          new Option('모든 주제', ''),
          ...data.topics.map((t) => new Option(t.title, t.id))
        );
        $('#topic-filter').value = topicValue;
      }
      renderImpacts(data);
      renderNews(data);
    } catch (error) {
      if (id !== state.request || (error.name === 'AbortError' && !timedOut)) return;
      const message = timedOut
        ? '뉴스 조회가 지연되고 있습니다. 잠시 후 다시 시도해 주세요.'
        : '뉴스를 불러오지 못했습니다. ' + error.message;
      $('#scope-label').textContent = selectedScope() + ' · ' + message;
      if (append) {
        state.page = Math.max(1, state.page - 1);
      } else {
        state.data = null;
        $('#news-count').textContent = '—';
        const box = node('div', 'empty', message),
          retry = node('button', 'button', '다시 시도');
        retry.type = 'button';
        retry.addEventListener('click', () => load());
        box.append(document.createElement('br'), retry);
        $('#news-grid').replaceChildren(box);
      }
    } finally {
      clearTimeout(timeout);
      if (id === state.request) {
        state.newsLoading = false;
        $('#refresh').disabled = false;
        $('#more-news').disabled = false;
        $('#news-grid').setAttribute('aria-busy', 'false');
      }
    }
  }
  let overviewRequest = null,
    overviewSignature = '';
  async function loadOverview(force = false) {
    if (overviewRequest) {
      if (!force) return overviewRequest;
      await overviewRequest;
    }
    overviewRequest = (async () => {
      try {
        const data = await api('/api/strategy?view=overview');
        state.overview = data;
        renderMetrics({ ...data, graph: state.graph || data.graph });
        const signature = JSON.stringify([data.trends, data.runs]);
        if (force || signature !== overviewSignature) {
          overviewSignature = signature;
          renderTrends(data);
          renderMonitoring(data.trends?.monitoring);
          topics.renderEvidence(data);
          renderOutlook({ ...data, runs: data.runs || [] });
          if (topics.data && $('#topic-management').open) topics.render(topics.data);
        }
        if (state.data && !state.newsLoading) renderNews(state.data);
        if ($('#topic-management').open) await topics.load(force);
        scheduleStatus('overview', () => loadOverview(), 60000);
      } catch (e) {
        notify(e.message, true);
        retryStatus('overview', () => loadOverview(), e, 60000);
      } finally {
        overviewRequest = null;
      }
    })();
    return overviewRequest;
  }

  async function loadGraphPreview() {
    try {
      state.graph = await api('/api/graph/integrated?max_nodes=16&evidence_limit=3&view=preview');
      renderGraph({ graph: state.graph });
      if (state.overview) renderMetrics({ ...state.overview, graph: state.graph });
    } catch (e) {
      $('#graph-detail').textContent = e.message;
    }
  }

  async function openStory(item) {
    if (item.item_id && !item._detail) {
      try {
        const data = await api('/api/strategy/item?id=' + encodeURIComponent(item.item_id));
        return openStory({ ...data.item, matched_terms: item.matched_terms, _detail: true });
      } catch (e) {
        notify(e.message, true);
        return;
      }
    }
    const dialog = $('#story-dialog'),
      area = $('#story-detail');
    area.replaceChildren();
    area.append(
      node('span', 'pill', `${item.day} · ${item.content_type_title || '뉴스'}`),
      highlightTopic(node('h2', '', item.title), item),
      node('h3', '', 'Telegram 수집 근거'),
      highlightTopic(node('p', '', item.text || item.excerpt || ''), item)
    );
    if (item.sectors?.length) {
      area.append(node('h3', '', '기술 분야 분류 근거'));
      item.sectors.forEach((s) =>
        area.append(node('p', '', s.label + ' · ' + (s.matched_terms || []).join(', ')))
      );
    }
    if (item.strategic_value) area.append(valueSummary(item.strategic_value, true));
    if (item.base_analysis) area.append(baselineDetail(item.base_analysis));
    const s = item.source_context || {};
    area.append(node('h3', '', '원문에서 추가로 확보한 정보'));
    if (s.status === 'fetched') {
      area.append(
        node(
          'span',
          'pill green',
          `원문 일부 조회 · ${s.fetched_at?.slice(0, 10) || ''}${s.truncated ? ' · 길이 제한' : ''}`
        ),
        highlightTopic(node('p', '', s.title || ''), item),
        highlightTopic(node('p', '', s.text), item)
      );
    } else {
      area.append(
        node(
          'p',
          'subtle',
          s.status
            ? `조회 상태: ${s.status}. ${s.error || ''}`
            : '원문은 아직 조회하지 않았습니다. 아래 버튼으로 짧은 본문 정보를 가져올 수 있습니다.'
        )
      );
    }
    const analysis = item.strategic_analysis;
    if (analysis) {
      if (analysis.freshness === 'historical_unchecked')
        area.append(node('p', 'subtle', '저장된 연구 해석 · 현재 원문 일치 미확인'));
      area.append(node('h3', '', '저장된 전략 분석'), node('p', '', analysis.summary));
      for (const point of analysis.implications || []) area.append(node('p', '', point.text));
      area.append(
        link(
          '문서 분석 근거 확인 ↗',
          `/research?run=${encodeURIComponent(analysis.run_id)}&document=${encodeURIComponent(analysis.doc_id)}`
        )
      );
    }
    const actions = node('div', 'heading-actions');
    if (item.source_url) {
      actions.append(
        link('기사 상세 해설 ↗', '/article?url=' + encodeURIComponent(item.source_url), 'button')
      );
      actions.append(link('원문 열기 ↗', item.source_url, 'button'));
      const fetchButton = node('button', 'button', '원문 정보 가져오기');
      fetchButton.addEventListener('click', () =>
        action(fetchButton, async () => {
          await api('/api/sources?date=all', { url: item.source_url, limit: 1 });
          notify('원문 조회를 시작했습니다. 처리 후 새로고침하면 키워드와 분석 근거에 반영됩니다.');
          dialog.close();
          pollSources();
        })
      );
      actions.append(fetchButton);
    }
    if (item.group_id) {
      const analyze = node('button', 'button primary', '의미 분석 실행');
      analyze.addEventListener('click', () =>
        action(analyze, async () => {
          await api(`/api/links/${item.group_id}/analyze`, {});
          notify('링크 의미 분석을 요청했습니다. 완료 후 상세 결과를 표시합니다.');
          const resultArea = node('div');
          area.append(resultArea);
          pollMeaning(item.group_id, resultArea);
        })
      );
      actions.append(analyze);
    }
    const watch = node('button', 'button', '관심 키워드로 저장');
    watch.addEventListener('click', () =>
      openWatch(
        item.strategic_keywords?.[0]?.label || '',
        item.strategic_keywords?.[0]?.label || ''
      )
    );
    actions.append(watch);
    area.append(actions);
    if (item.matched_terms?.length)
      area.append(node('p', 'subtle', '전략 주제 연결 단서: ' + item.matched_terms.join(', ')));
    if (!dialog.open) dialog.showModal();
  }
  let sourcePoll;
  function pollSources() {
    clearTimeout(sourcePoll);
    scheduleStatus(
      'sources',
      async () => {
        try {
          const s = await api('/api/sources');
          notify(
            `원문 조회: ${num(s.counts.fetched)}개 확보 · ${num(s.pending)}개 대기 · ${num(s.total - (s.counts.fetched || 0))}개 조회 제한/실패`
          );
          if (s.pending) pollSources();
          else {
            stopStatus('sources');
            await load();
          }
        } catch (e) {
          notify(e.message, true);
          retryStatus('sources', pollSources, e, 4000);
        }
      },
      4000
    );
  }
  async function pollMeaning(id, area, attempt = 0) {
    try {
      const data = await api('/api/links/' + id);
      const a = data.analysis || {};
      if (['queued', 'running'].includes(a.status) && attempt < 90) {
        area.textContent = '의미 분석 중…';
        scheduleStatus(
          'meaning-' + id,
          () => {
            if (area.isConnected) return pollMeaning(id, area, attempt + 1);
            else stopStatus('meaning-' + id);
          },
          4000
        );
        return;
      }
      stopStatus('meaning-' + id);
      area.replaceChildren(node('h3', '', '링크 의미 분석'));
      if (a.status === 'complete') {
        area.append(node('p', '', a.summary));
        (a.meaning || []).forEach((p) => area.append(node('p', '', p.text)));
      } else area.append(node('p', '', a.error || '분석 결과를 확인할 수 없습니다.'));
    } catch (e) {
      area.textContent = e.message;
      if (area.isConnected)
        retryStatus('meaning-' + id, () => pollMeaning(id, area, attempt), e, 4000);
    }
  }
  function renderGraphAnswer(result) {
    const area = $('#answer'),
      evidence = result.evidence || [],
      numbers = new Map(evidence.map((e, i) => [e.id, i + 1]));
    area.replaceChildren(node('p', '', result.answer || ''));
    const claims = node('ol', 'answer-claims');
    (result.claims || []).forEach((claim) => {
      const item = node('li', '', claim.text);
      (claim.evidence_ids || []).forEach((id) => {
        const number = numbers.get(id);
        if (!number) return;
        const a = link(` [근거 ${number}]`, '#strategy-answer-source-' + number);
        a.addEventListener('click', (event) => {
          event.preventDefault();
          const target = $('#strategy-answer-source-' + number);
          if (target) {
            target.closest('details').open = true;
            target.focus();
          }
        });
        item.append(a);
      });
      claims.append(item);
    });
    if (claims.childElementCount) area.append(claims);
    (result.limitations || []).forEach((t) => area.append(node('p', 'subtle', t)));
    if (evidence.length) {
      const detail = node('details');
      detail.append(node('summary', '', `답변 근거 ${evidence.length}개`));
      const list = node('ol');
      evidence.forEach((e, i) => {
        const item = node('li');
        item.id = 'strategy-answer-source-' + (i + 1);
        item.tabIndex = -1;
        item.append(
          link(e.title || '응답 근거', e.source_url || '/graph'),
          node('p', 'subtle', e.text || '')
        );
        list.append(item);
      });
      detail.append(list);
      area.append(detail);
    }
    if (result.timing)
      area.append(
        node(
          'p',
          'subtle',
          `응답 ${((result.timing.total_ms || result.timing.answer_ms || 0) / 1000).toFixed(1)}초${result.timing.answer_cache_hit ? ' · 같은 질문과 근거의 저장 답변' : ''}`
        )
      );
  }
  async function ask() {
    const button = $('#ask-button');
    if (button.disabled) return;
    await action(button, async () => {
      const controller = new AbortController(),
        started = Date.now();
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, Workspace.questionTimeout);
      const ticker = setInterval(() => {
        button.textContent = `답변 작성 중 · ${Math.floor((Date.now() - started) / 1000)}초`;
      }, 1000);
      $('#answer').textContent = '저장된 근거를 검색하고 답변을 검토하고 있습니다…';
      try {
        const first = await api(
          '/api/graph/ask',
          { question: $('#question').value, node_ids: state.selectedNodes },
          controller.signal
        );
        renderGraphAnswer(first);
        if (first.job) {
          let active = true;
          while (active) {
            button.textContent = '근거 표시 완료 · 심층 분석 중';
            await new Promise((resolve) => setTimeout(resolve, 2000));
            const job = await api(first.job.url, undefined, controller.signal);
            if (job.result?.answer) renderGraphAnswer(job.result);
            active = ['queued', 'running', 'preparing'].includes(job.status);
            if (job.error) {
              $('#answer').append(node('p', 'subtle', job.error));
            }
            if (job.status === 'stale') {
              $('#answer').replaceChildren(node('p', 'subtle', job.error));
            }
          }
        }
      } catch (e) {
        if (timedOut)
          e = new Error('답변 대기 시간이 길어 요청을 종료했습니다. 잠시 후 다시 시도해 주세요.');
        $('#answer').textContent = e.message;
        throw e;
      } finally {
        clearTimeout(timeout);
        clearInterval(ticker);
      }
    });
  }
  function saveWatches() {
    try {
      localStorage.setItem('ai-strategy-watchlist', JSON.stringify(watches));
    } catch (_) {
      notify('이 브라우저에서는 관심 목록을 저장할 수 없습니다.', true);
    }
    renderWatches();
  }
  function renderWatches() {
    $('#watchlist').replaceChildren(
      ...watches.map((w, i) => {
        const row = node('div', 'watch-item'),
          b = node('button', '', w.name),
          remove = node('button', '', '×');
        remove.setAttribute('aria-label', w.name + ' 관심 목록 삭제');
        b.addEventListener('click', () => {
          state.lens = w.lens || '';
          state.keyword = '';
          state.morph = '';
          state.terms = w.lens ? '' : w.terms || '';
          $('#search').value = '';
          state.visible = 9;
          load();
          $('#intelligence').scrollIntoView({ behavior: 'smooth' });
        });
        remove.addEventListener('click', () => {
          watches.splice(i, 1);
          saveWatches();
        });
        row.append(b, remove);
        return row;
      })
    );
  }
  function openWatch(name = '', terms = '') {
    $('#watch-name').value = name;
    $('#watch-terms').value = terms;
    $('#watch-dialog').showModal();
  }
  $('#watch-add').addEventListener('click', () => openWatch());
  $('#watch-cancel').addEventListener('click', () => $('#watch-dialog').close());
  $('#watch-form').addEventListener('submit', (e) => {
    e.preventDefault();
    watches.push({ name: $('#watch-name').value.trim(), terms: $('#watch-terms').value.trim() });
    watches = watches.slice(-20);
    saveWatches();
    $('#watch-dialog').close();
  });
  $('#close-dialog').addEventListener('click', () => $('#story-dialog').close());
  $('#refresh').addEventListener('click', () => {
    load();
    loadOverview();
    loadGraphPreview();
  });
  $('#more-news').addEventListener('click', () => {
    state.page++;
    route.write({ page: state.page }, 'push');
    load(true);
  });
  let searchTimer;
  $('#search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    state.keyword = '';
    state.morph = '';
    state.visible = 9;
    searchTimer = setTimeout(load, 400);
  });
  ['date-filter', 'topic-filter', 'impact-filter', 'sort-filter', 'sector-filter'].forEach((id) =>
    $('#' + id).addEventListener('change', () => {
      state.visible = 9;
      route.write({ page: 1 }, 'push');
      load();
    })
  );
  $('#clear-filters').addEventListener('click', () => {
    state.lens = '';
    state.terms = '';
    state.keyword = '';
    state.morph = '';
    $('#search').value = '';
    $('#date-filter').value = 'all';
    $('#topic-filter').value = '';
    $('#impact-filter').value = '';
    $('#sector-filter').value = '';
    $('#sort-filter').value = 'strategic';
    state.visible = 9;
    load();
  });
  $('#fetch-sources').addEventListener('click', () =>
    action($('#fetch-sources'), async () => {
      const r = await api('/api/sources?' + query(), { limit: 40 });
      notify(
        `현재 범위의 ${r.available}개 URL 중 현재 정렬의 상위 ${r.queued}개 원문을 조회합니다.`
      );
      pollSources();
    })
  );
  $('#analyze').textContent = '✦ 주요 24건 분석';
  $('#analyze').addEventListener('click', () =>
    action($('#analyze'), async () => {
      const r = await api('/api/research?' + query(), { sample_limit: 24 });
      notify(
        `현재 정렬의 상위 뉴스 최대 24건에서 ${r.run.total_documents}개 분석 문서를 구성했습니다. 종합 분석 보고서에서 진행 상황을 확인하세요.`
      );
      await load();
    })
  );
  $('#build-graph').addEventListener('click', () =>
    action($('#build-graph'), async () => {
      await api('/api/graph/analyze?' + query(), {});
      notify(
        '현재 범위에서 시간순 대표 근거 최대 24개로 관계 분석을 시작했습니다. 전체 관계 지도에서 완료 상태를 확인할 수 있습니다.'
      );
    })
  );
  $('#ask-form').addEventListener('submit', (e) => {
    e.preventDefault();
    ask();
  });
  document.querySelectorAll('[data-question]').forEach((b) =>
    b.addEventListener('click', () => {
      $('#question').value = b.dataset.question;
      state.selectedNodes = [];
      $('#network').scrollIntoView({ behavior: 'smooth' });
      $('#question').focus();
    })
  );
  $('#simulation-draft').addEventListener('click', () =>
    action($('#simulation-draft'), async () => {
      const r = await api('/api/simulation', {
        title: (state.lens || 'AI') + ' 전략 시나리오',
        requirement: $('#hypothesis').value,
        rounds: 2,
        platform: 'twitter',
        filters: {
          date: $('#date-filter').value,
          topic: $('#topic-filter').value,
          q: $('#search').value,
          lens: state.lens,
          terms: state.terms,
          keyword: state.keyword,
          strategic_keyword: state.morph,
          impact: $('#impact-filter').value,
          sector: $('#sector-filter').value,
          sort: $('#sort-filter').value,
        },
        limit: 20,
      });
      location.href = '/simulation?run=' + encodeURIComponent(r.run.id);
    })
  );
  async function runtime() {
    try {
      const r = await api('/api/simulation'),
        s = r.runtime;
      $('#simulation-status').textContent =
        s.installed && s.configured
          ? '엔진 준비됨'
          : !s.installed
            ? '실행 환경 확인 필요'
            : '서비스 설정 필요';
      $('#simulation-status').className =
        'pill ' + (s.installed && s.configured ? 'green' : 'amber');
    } catch (_) {
      $('#simulation-status').textContent = '상태 확인 실패';
    }
  }

  const menu = $('#menu-toggle');
  menu.addEventListener('click', () => {
    const open = $('#sidebar').classList.toggle('expanded');
    menu.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('click', (e) => {
    if (!$('#sidebar').contains(e.target) && !menu.contains(e.target)) {
      $('#sidebar').classList.remove('expanded');
      menu.setAttribute('aria-expanded', 'false');
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      $('#sidebar').classList.remove('expanded');
      menu.setAttribute('aria-expanded', 'false');
    }
  });

  $('#workflow-details').addEventListener('click', () => {
    workflow.details();
  });
  $('#improvement-details').addEventListener('click', () => {
    improvement.details();
  });
  async function loadStrategicChanges() {
    const area = $('#strategic-change-list');
    try {
      const data = await api('/api/intelligence?view=overview&page_size=8');
      area.replaceChildren();
      const changes = (data.changes || data.items || []).slice(0, 8);
      if (!changes.length)
        area.append(
          node(
            'p',
            'subtle',
            '아직 우선 변화가 없습니다. 사건·주제별 검토 상태는 전략 검토실에서 확인하세요.'
          )
        );
      changes.forEach((item) => {
        const row = node('div', 'strategic-change-row');
        row.append(
          link(item.title || item.label || '전략 변화', item.href || '/intelligence'),
          node('span', 'subtle', item.summary || item.description || '')
        );
        area.append(row);
      });
    } catch (error) {
      area.replaceChildren(node('p', 'subtle', '전략 변화 조회: ' + error.message));
    }
  }
  const { improvementText, improvementEvidence } = StrategyPanels.evidence({ node, link });
  function baselineDetail(analysis) {
    return baseline.detail(analysis);
  }
  const reading = {
    get showAllTopics() {
      return showAllTopics;
    },
    set showAllTopics(v) {
      showAllTopics = v;
    },
    get showAllCandidates() {
      return showAllCandidates;
    },
    set showAllCandidates(v) {
      showAllCandidates = v;
    },
    get showAllSignals() {
      return showAllSignals;
    },
    set showAllSignals(v) {
      showAllSignals = v;
    },
    get monitoringData() {
      return monitoringData;
    },
    set monitoringData(v) {
      monitoringData = v;
    },
  };
  const panelContext = {
    $,
    node,
    num,
    safe,
    link,
    notify,
    api,
    action,
    query,
    state,
    scheduleStatus,
    stopStatus,
    retryStatus,
    load,
    loadOverview,
    selectLens,
    selectedScope,
    empty,
    spark,
    svgEl,
    openStory,
    growth,
    improvementText,
    improvementEvidence,
    reading,
    renderMonitoring,
    renderTrends,
    renderNews,
    visibleLenses,
    loadWorkflow: (...args) => workflow.load(...args),
  };
  const workflow = StrategyPanels.workflow(panelContext);
  const improvement = StrategyPanels.improvement(panelContext);
  const baseline = StrategyPanels.baseline(panelContext);
  const topics = StrategyPanels.topics(panelContext);
  const risks = StrategyPanels.risks(panelContext);
  loadCorpusStatus();
  loadChannelStatus();
  renderLensTabs();
  renderWatches();
  if (operationsMode) {
    runtime();
    workflow.load();
    improvement.load();
    baseline.load();
  } else {
    load();
    loadOverview();
  }
  function observeSection(selector, loadSection) {
    if (!('IntersectionObserver' in window)) {
      loadSection();
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          observer.disconnect();
          loadSection();
        }
      },
      { rootMargin: '250px' }
    );
    observer.observe($(selector));
  }
  if (!operationsMode) {
    observeSection('#network', loadGraphPreview);
    observeSection('#risk-observatory', risks.load);
    observeSection('#strategic-changes', loadStrategicChanges);
  }
})();
