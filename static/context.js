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
    $('#status').textContent = '현재 근거 연결을 확인하고 있습니다…';
    try {
      const r = await fetch(
        '/api/context?' + new URLSearchParams({ q, ...(focus ? { node: focus } : {}) }),
        { signal: controller.signal }
      );
      const data = await r.json();
      if (version !== serial) return;
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
      if (e.name !== 'AbortError')
        $('#status').textContent = e.message + ' 종합 질의 버튼으로 다시 시도할 수 있습니다.';
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
    focus = '';
    const p = new URLSearchParams(location.search);
    for (const key of ['answer', 'question', 'forecast']) p.delete(key);
    history.replaceState(null, '', '/context?' + p);
    $('#citations-only').checked = false;
    load();
    document.dispatchEvent(new CustomEvent('context:query', { detail: { query } }));
  };
  $('#context-example').onclick = () => {
    $('#query').value = '최근 Physical AI 관련 벤치마크 방법 연구 현황과 데이터세트 종류, 이슈는 ?';
    $('#query').focus();
  };
  $('#query').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) {
      e.preventDefault();
      $('#search').requestSubmit();
    }
  });
  $('#back').onclick = () => {
    focus = '';
    load();
  };
  $('#citations-only').onchange = render;
  for (const t of ['의료 AI', '공공 AX', '에이전트', 'AI 인프라', '소버린 AI'])
    $('#suggestions').append(
      button(t, () => {
        $('#query').value = t;
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
  $('#context-example').disabled = false;
  load();
})();
