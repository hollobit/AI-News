(() => {
  'use strict';
  const $ = (s) => document.querySelector(s),
    el = (tag, text, cls) => {
      const n = document.createElement(tag);
      n.textContent = text || '';
      if (cls) n.className = cls;
      return n;
    };
  let controller = null,
    timer = null,
    started = 0;
  const labels = {
    finding: '확인된 자료의 내용',
    connection: '자료 사이의 관계',
    insight: '핵심 의미·전략적 통찰',
  };
  function safeLink(text, url) {
    const a = el('a', text);
    try {
      const u = new URL(url, location.origin);
      if (['http:', 'https:'].includes(u.protocol)) {
        a.href = u.href;
        a.rel = 'noopener noreferrer';
      }
    } catch (_) {}
    return a;
  }
  function render(data, complete = false) {
    const root = $('#context-answer');
    root.replaceChildren();
    if (!complete) {
      root.append(
        el('p', '관련 원문을 먼저 제공합니다. 아래 발췌는 최종 종합 답변이 아닙니다.', 'legend')
      );
    }
    const reviewed = complete && data.verification?.method === 'independent_evidence_review';
    if (reviewed) {
      root.append(el('h3', '종합 판단'), el('p', data.answer, 'answer-synthesis'));
      if (data.analysis_plan?.axes?.length)
        root.append(el('p', '검토한 축 · ' + data.analysis_plan.axes.join(' / '), 'legend'));
    } else if (complete)
      root.append(el('p', data.answer || '현재 확보한 자료만으로는 질문에 답하기 어렵습니다.'));
    const references = new Map(
      (data.evidence || []).map((e, i) => [e.id, { ...e, number: i + 1 }])
    );
    if (reviewed) {
      for (const kind of ['finding', 'connection', 'insight']) {
        const items = (data.claims || []).filter((c) => (c.kind || 'finding') === kind);
        const section = el('section', null, 'answer-section');
        section.append(el('h3', labels[kind]));
        if (!items.length)
          section.append(el('p', '현재 근거로 독립 검토를 통과한 내용이 없습니다.', 'legend'));
        for (const c of items) {
          const card = el('article', null, 'card');
          card.append(el('h4', c.axis || labels[kind]), el('p', c.text));
          for (const [key, label] of [
            ['premises', '근거에서 해석까지'],
            ['assumptions', '성립 조건'],
            ['counterevidence', '상충·반대 근거'],
            ['uncertainty', '판단의 한계·변경 조건'],
          ])
            if (c[key]) card.append(el('strong', label), el('p', c[key]));
          const cites = el('div', null, 'actions');
          for (const ref of c.evidence_ids || []) {
            const e = references.get(ref);
            if (e)
              cites.append(safeLink('[' + e.number + '] ' + e.title, '#answer-source-' + e.number));
          }
          card.append(cites);
          section.append(card);
        }
        root.append(section);
      }
    }
    for (const text of data.limitations || []) root.append(el('p', text, 'legend'));
    const sources = el('details');
    sources.open = !complete;
    sources.append(
      el('summary', `인용 원자료 ${references.size}개 · 뉴스/논문과 기존 해석의 구분`)
    );
    for (const e of references.values()) {
      const item = el('article', null, 'source');
      item.id = 'answer-source-' + e.number;
      item.append(
        safeLink('[' + e.number + '] ' + e.title, e.source_url || e.url || ''),
        el(
          'p',
          `${e.day || e.published_at || '날짜 미상'} · ${{ paper: '논문 원자료', reviewed_paper_evidence: '검토 논문 원자료', workflow: '뉴스 상세 분석의 근거', baseline: '뉴스 기본 분석의 근거', wiki: '지식의 원자료', external: '외부 원문' }[e.source_kind] || '저장된 출처 발췌'} · 초록/발췌 범위의 내용`
        ),
        el('p', e.text)
      );
      sources.append(item);
    }
    root.append(sources);
  }
  async function get(url, options = {}) {
    const r = await fetch(url, { ...options, signal: controller.signal });
    const d = await r.json();
    if (!r.ok) throw Error(d.error || '분석 요청 실패');
    return d;
  }
  async function poll(url) {
    try {
      const job = await get(url);
      if (job.status === 'complete') {
        render(job.result, true);
        $('#answer-status').textContent = '관계 분석·독립 근거 검토 완료';
        $('#context-ask-button').disabled = false;
        return;
      }
      if (['failed', 'stale', 'interrupted'].includes(job.status))
        throw Error(job.error || '분석이 중단되었습니다. 다시 요청하세요.');
      if (job.result?.evidence?.length) render(job.result, false);
      if (job.status === 'evidence_only') {
        render(job.result, true);
        $('#answer-status').textContent = '원자료만 제공 · 검토된 종합 답변 없음';
        $('#context-ask-button').disabled = false;
        return;
      }
      $('#answer-status').textContent =
        `근거 검색 → 분석 축별 종합 → 관계·통찰 검토 중 · ${Math.floor((Date.now() - started) / 1000)}초`;
      timer = setTimeout(() => poll(url), 3000);
    } catch (e) {
      if (e.name !== 'AbortError') $('#answer-status').textContent = e.message;
      $('#context-ask-button').disabled = false;
    }
  }
  document.addEventListener('context:query', async (e) => {
    const question = e.detail.query;
    $('#active-question').textContent = '분석 질의 · ' + question;
    if (question.length < 3) {
      $('#answer-status').textContent = '3자 이상 질문을 입력하세요.';
      return;
    }
    controller?.abort();
    clearTimeout(timer);
    controller = new AbortController();
    started = Date.now();
    $('#context-ask-button').disabled = true;
    $('#answer-status').textContent = '뉴스·논문·위험·지식의 최신 연결 근거를 찾고 있습니다…';
    $('#context-answer').replaceChildren();
    try {
      const d = await get('/api/graph/ask?workspace=1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, node_ids: [] }),
      });
      render(d, Boolean(d.verification?.method === 'independent_evidence_review'));
      if (d.job) {
        const p = new URLSearchParams(location.search);
        p.set('answer', d.job.id);
        p.set('q', question);
        p.delete('question');
        history.replaceState(null, '', '/context?' + p);
        poll(d.job.url);
      } else {
        $('#context-ask-button').disabled = false;
        $('#answer-status').textContent =
          d.verification?.method === 'independent_evidence_review'
            ? '검토된 종합 답변'
            : '현재 확보된 근거 범위의 응답';
      }
    } catch (e) {
      if (e.name !== 'AbortError') $('#answer-status').textContent = e.message;
      $('#context-ask-button').disabled = false;
    }
  });
  const initial = new URLSearchParams(location.search);
  const initialQuery = $('#query').value;
  if (initialQuery) $('#active-question').textContent = '질의 · ' + initialQuery;
  if (/^[a-f0-9]{32}$/.test(initial.get('answer') || '')) {
    controller = new AbortController();
    started = Date.now();
    $('#context-ask-button').disabled = true;
    poll('/api/graph/answers/' + initial.get('answer'));
  } else {
    $('#context-ask-button').disabled = false;
  }
  addEventListener('pagehide', () => {
    controller?.abort();
    clearTimeout(timer);
  });
})();
