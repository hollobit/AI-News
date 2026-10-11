(() => {
  'use strict';
  const $ = (s) => document.querySelector(s),
    make = (tag, text, cls) => {
      const e = document.createElement(tag);
      if (text) e.textContent = text;
      if (cls) e.className = cls;
      return e;
    };
  const selected = new Map();
  let lastRevision = '',
    lastQuery = '',
    retries = 0;
  let current = null,
    controller = null,
    timer = null,
    serial = 0;
  const params = new URLSearchParams(location.search);
  let focus = params.get('node') || '';
  let activeQuery = (params.get('question') || params.get('q') || '').slice(0, 2000);
  $('#query').value = activeQuery;
  const exampleTopics = ['Physical AI', '의료 AI', '공공 AX', '에이전트', 'AI 인프라', '소버린 AI'];
  const exampleAngles = [
    (topic) => `최근 ${topic} 관련 뉴스와 논문에서 공통으로 나타나는 변화와 쟁점은 무엇인가요?`,
    (topic) => `${topic}의 실제 도입 사례와 연구 결과를 비교하고, 기대 효과와 위험을 분석해주세요.`,
    (topic) => `${topic} 관련 자료들의 상충하는 주장은 무엇이며, 어떤 조건에서 판단이 달라지나요?`,
    (topic) => `${topic}의 핵심 기술과 평가 방법, 데이터의 한계를 연결해 설명해주세요.`,
  ];
  let currentExample = '';
  function showExample(topic) {
    currentExample = exampleAngles[Math.floor(Math.random() * exampleAngles.length)](topic);
    $('#query').placeholder = currentExample;
    $('#query-example').textContent = '예시 질문 · ' + currentExample;
  }
  showExample(exampleTopics[Math.floor(Math.random() * exampleTopics.length)]);
  const historyKey = 'news.context.recent-queries.v1';
  let recentQueries = [];
  function rememberQueries(items) {
    const unique = new Map();
    for (const item of [...recentQueries, ...items]) {
      if (!item || typeof item.question !== 'string' || !Number.isFinite(item.used_at)) continue;
      const question = item.question.trim();
      if (question.length < 3 || question.length > 2000) continue;
      if (!unique.has(question) || unique.get(question).used_at < item.used_at)
        unique.set(question, { question, used_at: item.used_at });
    }
    recentQueries = [...unique.values()].sort((a, b) => b.used_at - a.used_at).slice(0, 10);
    try {
      localStorage.setItem(historyKey, JSON.stringify(recentQueries));
    } catch (_) {}
    $('#query-history').hidden = !recentQueries.length;
    $('#query-history-items').replaceChildren(
      ...recentQueries.map((item) => {
        const entry = button(item.question, () => {
          $('#query').value = item.question;
          $('#query').focus();
        });
        entry.title = item.question;
        return entry;
      })
    );
  }
  try {
    const saved = JSON.parse(localStorage.getItem(historyKey) || '[]');
    if (Array.isArray(saved)) rememberQueries(saved);
  } catch (_) {}
  fetch('/api/context/history')
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (Array.isArray(data?.items)) rememberQueries(data.items);
    })
    .catch(() => {});
  function link(text, url) {
    const a = make('a', text);
    try {
      const u = new URL(url, location.origin);
      if (['http:', 'https:'].includes(u.protocol)) {
        a.href = u.href;
        a.rel = 'noopener noreferrer';
      }
    } catch (_) {}
    return a;
  }
  function button(text, fn) {
    const b = make('button', text);
    b.type = 'button';
    b.onclick = fn;
    return b;
  }
  function choose(c) {
    if (selected.has(c.id)) selected.delete(c.id);
    else if (selected.size < 4) selected.set(c.id, c);
    else {
      $('#status').textContent = '최대 4개 항목을 선택해 비교할 수 있습니다.';
      return;
    }
    render();
  }
  function card(c, comparison = false) {
    const e = make('article', null, 'card');
    e.append(
      make('span', (c.status_label ? c.status_label + ' · ' : '') + c.reason, 'reason'),
      make('h3', c.title),
      make('p', c.summary)
    );
    const details = make('details'),
      summary = make(
        'summary',
        `근거 ${c.sources.length}개${c.source_truncated ? ' (일부 표시)' : ''}`
      );
    details.append(summary);
    for (const s of c.sources) {
      const row = make('div', null, 'source');
      row.append(
        link(s.title || '원자료', s.url),
        make(
          'p',
          `${s.day || '날짜 미상'} · ${{ arxiv_abstract: '공식 논문 초록', arxiv_metadata: '공식 논문 메타데이터', arxiv_html_excerpt: '논문 본문 발췌', scholarly_index_abstract: '외부 학술 색인 초록', scholarly_index_metadata: '외부 학술 색인 메타데이터', telegram_excerpt: 'Telegram 발췌', fetched_url_excerpt: '확보 원문 발췌' }[s.origin] || '저장 원자료'} · ${{ workflow: '상세 분석 근거', baseline: '기본 분석 근거', paper: '논문 근거', wiki: '지식 원자료', external: '확보 원문' }[s.kind] || '원자료'} · ${{ accepted_analysis: '해석 독립 검토 통과', accepted_excerpt_analysis: '기본 해석 검토 통과', accepted_paper_analysis: '논문 해석 검토 통과', unreviewed_source: '원자료 관측' }[s.verification] || '원자료 관측 · 사실성 보증 아님'}`
        ),
        make('p', s.excerpt)
      );
      details.append(row);
    }
    e.append(details);
    ObservatorySearch.highlightQuestion(e, activeQuery);
    const actions = make('div', null, 'actions'),
      pick = button(selected.has(c.id) ? '비교에서 제거' : '비교에 담기', () => choose(c));
    pick.setAttribute('aria-pressed', String(selected.has(c.id)));
    actions.append(
      pick,
      button('이 항목 중심으로', () => {
        focus = c.id;
        load();
      }),
      link('상세 화면', c.href)
    );
    e.append(actions);
    return e;
  }
  function render() {
    if (!current) return;
    $('#groups').replaceChildren();
    $('#back').hidden = !focus;
    $('#citations-only').disabled = !focus;
    for (const [kind, label] of Object.entries(current.labels || {})) {
      const lane = make('section', null, 'lane');
      lane.dataset.kind = kind;
      const h = make('h2', label);
      h.append(make('span', String((current.groups[kind] || []).length), 'count'));
      lane.append(h);
      const rows = (current.groups[kind] || []).filter(
        (c) =>
          !$('#citations-only').checked ||
          !focus ||
          (!c.reason.includes('관련성 후보') && !c.reason.includes('동일 원문 URL 연결'))
      );
      for (const c of rows) lane.append(card(c));
      if (!rows.length)
        lane.append(make('p', '현재 탐색 범위에서 연결된 근거가 없습니다.', 'empty'));
      $('#groups').append(lane);
    }
    $('#comparison').hidden = !selected.size;
    $('#comparison').replaceChildren();
    $('#export').disabled = !selected.size;
    if (selected.size) {
      $('#comparison').append(
        make('h2', `선택 비교 · ${selected.size}/4`),
        make(
          'p',
          '각 해석의 근거 날짜·출처·조건을 대조하세요. 같은 출처를 인용한 항목은 별개의 독립 근거가 아닙니다.'
        )
      );
      const grid = make('div', null, 'compare-grid');
      for (const c of selected.values()) grid.append(card(c, true));
      $('#comparison').append(grid);
      const unique = new Map();
      for (const c of selected.values())
        for (const source of c.sources) {
          const key = source.url || source.id;
          if (!unique.has(key)) unique.set(key, { ...source, uses: 0 });
          unique.get(key).uses++;
        }
      const timeline = make('details');
      timeline.append(make('summary', `근거 시간순 대조 · 표시된 고유 원문 ${unique.size}개`));
      for (const source of [...unique.values()].sort((a, b) =>
        (a.day || '9999').localeCompare(b.day || '9999')
      )) {
        const row = make('p');
        row.append(
          make('span', `${source.day || '날짜 미상'} · ${source.uses > 1 ? '공유 출처 · ' : ''}`),
          link(source.title || '원자료', source.url)
        );
        ObservatorySearch.highlightQuestion(row, activeQuery);
        timeline.append(row);
      }
      $('#comparison').append(timeline);
    }
    $('#scope').textContent = current.scope || '';
    $('#limitations').replaceChildren(...(current.limitations || []).map((t) => make('li', t)));
  }
  async function load(retry = false) {
    clearTimeout(timer);
    controller?.abort();
    controller = new AbortController();
    const version = ++serial;
    const q = activeQuery;
    const p = new URLSearchParams(location.search);
    p.delete('q');
    p.delete('question');
    p.delete('node');
    if (q) p.set('q', q);
    if (focus) p.set('node', focus);
    history.replaceState(null, '', '/context' + (p.size ? '?' + p : ''));
    if (!retry) {
      retries = 0;
      if (q !== lastQuery) selected.clear();
      lastQuery = q;
      current = null;
      $('#groups').replaceChildren();
      $('#comparison').hidden = true;
      $('#export').disabled = true;
    }
    $('#context-reload').hidden = true;
    $('#status').textContent = '현재 근거 연결을 확인하고 있습니다…';
    try {
      const r = await fetch(
        '/api/context?' + new URLSearchParams({ q, ...(focus ? { node: focus } : {}) }),
        { signal: controller.signal }
      );
      const data = await r.json().catch(() => ({}));
      if (version !== serial) return;
      if ([408, 429].includes(r.status) || r.status >= 500) {
        if (++retries <= 60) {
          $('#status').textContent =
            '자료 조회 연결이 지연되고 있습니다. 기존 질의를 자동으로 다시 조회합니다.';
          timer = setTimeout(() => load(true), 5000);
          return;
        }
      }
      if (r.status === 202) {
        if (++retries > 60) throw Error('색인 준비가 길어지고 있습니다. 잠시 후 다시 조회하세요.');
        $('#status').textContent = data.message + ' 자동 갱신 중';
        timer = setTimeout(() => load(true), 5000);
        return;
      }
      if (!r.ok) throw Error(data.error || '조회 실패');
      if (lastRevision && data.revision !== lastRevision) selected.clear();
      lastRevision = data.revision || '';
      current = data;
      $('#status').textContent = focus
        ? '선택 항목의 인용·동일 원문 연결입니다.'
        : '검색 관련성 후보입니다. 항목을 선택해 실제 근거 연결을 확인하세요.';
      render();
    } catch (e) {
      if (version !== serial || e.name === 'AbortError') return;
      if (e instanceof TypeError && ++retries <= 60) {
        $('#status').textContent =
          '자료 조회 연결을 복구하고 있습니다. 잠시 후 자동으로 다시 조회합니다.';
        timer = setTimeout(() => load(true), 5000);
        return;
      }
      $('#status').textContent = e.message;
      $('#context-reload').hidden = false;
    }
  }
  $('#search').onsubmit = (e) => {
    e.preventDefault();
    const query = $('#query').value.trim();
    if (query.length < 3 || query.length > 2000) {
      $('#answer-status').textContent = '질의는 3~2000자로 입력하세요.';
      return;
    }
    if ($('#context-ask-button').disabled) return;
    activeQuery = query;
    rememberQueries([{ question: query, used_at: Date.now() / 1000 }]);
    focus = '';
    const p = new URLSearchParams(location.search);
    for (const key of ['answer', 'question', 'forecast']) p.delete(key);
    history.replaceState(null, '', '/context?' + p);
    $('#citations-only').checked = false;
    load();
    document.dispatchEvent(new CustomEvent('context:query', { detail: { query } }));
  };
  $('#query').addEventListener('keydown', (e) => {
    if (
      ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key) &&
      !e.isComposing &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey &&
      !e.shiftKey &&
      !e.currentTarget.value.trim()
    ) {
      e.preventDefault();
      e.currentTarget.value = currentExample;
      e.currentTarget.setSelectionRange(currentExample.length, currentExample.length);
      return;
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) {
      e.preventDefault();
      $('#search').requestSubmit();
    }
  });
  $('#back').onclick = () => {
    focus = '';
    load();
  };
  $('#context-reload').onclick = () => load();
  $('#citations-only').onchange = render;
  for (const t of ['의료 AI', '공공 AX', '에이전트', 'AI 인프라', '소버린 AI'])
    $('#suggestions').append(
      button(t, () => {
        $('#query').value = t;
        showExample(t);
        $('#query').focus();
      })
    );
  $('#export').onclick = () => {
    const text = [
      '# 통합 분석 비교',
      `조회: ${new Date().toISOString()}`,
      current.scope,
      ...[...selected.values()].flatMap((c) => [
        '\n## ' + c.title,
        c.reason,
        c.summary,
        ...c.sources.map((s) => `- ${s.day || '날짜 미상'} | ${s.title} | ${s.url}`),
      ]),
    ].join('\n\n');
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    const a = link('내보내기', '');
    a.href = url;
    a.download = 'analysis-comparison.md';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  addEventListener('pagehide', () => {
    clearTimeout(timer);
    controller?.abort();
  });
  addEventListener('pageshow', (event) => {
    if (event.persisted) load(true);
  });
  load();
})();
