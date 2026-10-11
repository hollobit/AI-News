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
    started = 0,
    activeUrl = '',
    lastQuestion = '',
    failures = 0,
    shownEvidence = '';
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
    if (!complete && !data.evidence?.length) return;
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
    ObservatorySearch.highlightQuestion(root, lastQuestion);
  }
  function progress(stage, message, detail = '') {
    $('#answer-metrics').hidden = true;
    const steps = [...$('#answer-progress').children];
    const index = steps.findIndex((n) => n.dataset.stage === stage);
    $('#answer-progress').hidden = index < 0;
    steps.forEach((node, i) => {
      node.dataset.done = String(i < index);
      if (i === index) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    });
    $('#answer-status').textContent = message;
    $('#answer-detail').textContent = detail;
  }
  function metrics(job) {
    const data = job.progress;
    if (!data) return;
    $('#answer-metrics').hidden = false;
    $('#answer-meter').value = data.percent;
    $('#answer-count').textContent =
      `완료 단계 ${data.completed}/${data.total} · ${data.percent}% · 전체 경과 ${data.elapsed_seconds}초`;
    $('#answer-progress').hidden = false;
    $('#answer-progress').replaceChildren(
      ...data.stages.map((stage) => {
        const item = el(
          'li',
          stage.label + (stage.seconds === null ? '' : ` · ${stage.seconds}초`)
        );
        item.dataset.stage = stage.id;
        item.dataset.done = String(stage.status === 'complete');
        if (stage.status === 'running') item.setAttribute('aria-current', 'step');
        return item;
      })
    );
    if (job.status === 'complete') {
      $('#answer-eta').textContent = '모든 단계 완료';
      return;
    }
    const current = data.stages
      .filter((stage) => stage.status === 'running')
      .map((stage) => stage.label);
    $('#answer-status').textContent = current.length
      ? current.join(' + ') + ' 진행 중'
      : '분석 작업 순서를 기다리고 있습니다';
    $('#answer-detail').textContent =
      '진행률은 실제 완료한 단계의 비율입니다. 단계마다 소요 시간은 다릅니다.';
    $('#answer-eta').textContent = data.estimate_overdue
      ? '최근 작업보다 오래 걸리고 있습니다. 현재 단계가 끝나면 자동으로 갱신됩니다.'
      : data.remaining_seconds === null
        ? '남은 예상 시간 · 새 처리 경로의 실측 기록을 모으고 있습니다'
        : `남은 예상 약 ${data.remaining_seconds}초 · 최근 ${data.estimate_samples}건 실측 기준의 참고값`;
  }
  function controls() {
    for (const id of ['answer-resume', 'answer-retry', 'answer-login']) $('#' + id).hidden = true;
  }
  function finish() {
    activeUrl = '';
    clearTimeout(timer);
    $('#context-ask-button').disabled = false;
  }
  function retryQuestion() {
    $('#query').value = lastQuestion;
    $('#search').requestSubmit();
  }
  async function get(url, owner, options = {}) {
    if (owner.signal.aborted) throw new DOMException('Aborted', 'AbortError');
    const request = new AbortController();
    const abort = () => request.abort();
    owner.signal.addEventListener('abort', abort, { once: true });
    const timeout = setTimeout(abort, 20000);
    try {
      const r = await fetch(url, { ...options, signal: request.signal });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !Object.keys(d).length)
        throw Object.assign(Error(d.error || '결과 조회 연결이 지연되고 있습니다.'), {
          retryable: [408, 429].includes(r.status) || r.status >= 500 || r.ok,
          unauthorized: r.status === 401,
        });
      return d;
    } catch (e) {
      if (owner.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (e.retryable === undefined) e.retryable = true;
      throw e;
    } finally {
      clearTimeout(timeout);
      owner.signal.removeEventListener('abort', abort);
    }
  }
  async function poll(url, owner = controller) {
    if (controller !== owner || owner.signal.aborted) return;
    try {
      const job = await get(url, owner);
      if (controller !== owner || owner.signal.aborted) return;
      failures = 0;
      controls();
      if (job.status === 'complete') {
        render(job.result, true);
        progress('complete', '관계 분석·독립 근거 검토 완료');
        metrics(job);
        finish();
        return;
      }
      if (['failed', 'stale', 'interrupted'].includes(job.status)) {
        $('#context-answer').replaceChildren();
        progress(
          '',
          job.error || '분석이 중단되었습니다.',
          '아래 버튼으로 같은 질문을 다시 분석할 수 있습니다.'
        );
        finish();
        $('#answer-retry').hidden = !lastQuestion;
        return;
      }
      if (job.result?.evidence?.length) {
        const signature = JSON.stringify(job.result.evidence);
        if (shownEvidence !== signature) render(job.result, false);
        shownEvidence = signature;
      }
      if (job.status === 'evidence_only') {
        render(job.result, true);
        progress('', '원자료만 제공 · 검토된 종합 답변 없음');
        finish();
        return;
      }
      const elapsed = `조회 ${Math.floor((Date.now() - started) / 1000)}초`;
      if (job.status === 'preparing') {
        shownEvidence = '';
        $('#context-answer').replaceChildren();
        progress(
          'preparing',
          '최신 근거의 연결 색인을 준비하고 있습니다 · ' + elapsed,
          '자료가 갱신되면 색인 준비에 수 분이 걸릴 수 있습니다. 준비가 끝나면 종합 분석이 자동으로 이어집니다.'
        );
      } else if (job.status === 'queued') {
        progress('running', '근거 준비 완료 · 분석 작업 순서를 기다리고 있습니다 · ' + elapsed);
      } else {
        progress(
          'running',
          '답변 생성·관계 분석·독립 근거 검토 중 · ' + elapsed,
          '검토를 통과한 종합 답변이 이곳에 자동으로 표시됩니다. 새로고침해도 같은 작업을 이어서 확인합니다.'
        );
      }
      metrics(job);
      timer = setTimeout(() => poll(url, owner), 3000);
    } catch (e) {
      if (controller !== owner || owner.signal.aborted) return;
      if (e.retryable && ++failures <= 8) {
        const delay = Math.min(15000, 2000 * failures);
        $('#answer-status').textContent =
          '연결이 잠시 끊겼습니다 · 기존 분석 결과를 다시 조회합니다';
        $('#answer-detail').textContent =
          `${delay / 1000}초 후 자동 연결 · 새 분석을 중복 실행하지 않습니다.`;
        timer = setTimeout(() => poll(url, owner), delay);
        return;
      }
      progress(
        '',
        e.unauthorized ? '원격 로그인 시간이 만료되었습니다.' : e.message,
        '저장된 작업의 진행 상태를 다시 확인할 수 있습니다.'
      );
      $('#context-ask-button').disabled = false;
      if (e.unauthorized) {
        $('#answer-login').href = location.href;
        $('#answer-login').hidden = false;
        activeUrl = '';
      } else if (e.retryable) $('#answer-resume').hidden = false;
      else {
        activeUrl = '';
        $('#answer-retry').hidden = !lastQuestion;
      }
    }
  }
  function resume() {
    if (!activeUrl) return;
    controller?.abort();
    clearTimeout(timer);
    controller = new AbortController();
    failures = 0;
    controls();
    $('#context-ask-button').disabled = true;
    poll(activeUrl);
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
    const owner = controller;
    started = Date.now();
    lastQuestion = question;
    activeUrl = '';
    shownEvidence = '';
    failures = 0;
    controls();
    $('#context-ask-button').disabled = true;
    progress('preparing', '뉴스·논문·위험·지식의 최신 연결 근거를 찾고 있습니다…');
    $('#context-answer').replaceChildren();
    try {
      const d = await get('/api/graph/ask?workspace=1', owner, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, node_ids: [] }),
      });
      if (controller !== owner || owner.signal.aborted) return;
      render(d, Boolean(d.verification?.method === 'independent_evidence_review'));
      if (d.job) {
        const p = new URLSearchParams(location.search);
        p.set('answer', d.job.id);
        p.set('q', question);
        p.delete('question');
        history.replaceState(null, '', '/context?' + p);
        activeUrl = '/api/graph/answers/' + d.job.id;
        poll(activeUrl, owner);
      } else {
        finish();
        const reviewed = d.verification?.method === 'independent_evidence_review';
        progress(
          reviewed ? 'complete' : '',
          reviewed ? '검토된 종합 답변' : '현재 확보된 근거 범위의 응답'
        );
      }
    } catch (e) {
      if (controller !== owner || owner.signal.aborted) return;
      progress('', e.unauthorized ? '원격 로그인이 필요합니다.' : e.message);
      finish();
      if (e.unauthorized) {
        $('#answer-login').href = location.href;
        $('#answer-login').hidden = false;
      } else $('#answer-retry').hidden = false;
    }
  });
  const initial = new URLSearchParams(location.search);
  const initialQuery = $('#query').value;
  lastQuestion = initialQuery;
  if (initialQuery) $('#active-question').textContent = '질의 · ' + initialQuery;
  if (/^[a-f0-9]{32}$/.test(initial.get('answer') || '')) {
    started = Date.now();
    activeUrl = '/api/graph/answers/' + initial.get('answer');
    progress('preparing', '저장된 분석 작업의 진행 상태를 확인합니다…');
    resume();
  } else {
    $('#context-ask-button').disabled = false;
  }
  $('#answer-resume').onclick = resume;
  $('#answer-retry').onclick = retryQuestion;
  addEventListener('pageshow', (event) => {
    if (event.persisted) resume();
  });
  addEventListener('online', resume);
  addEventListener('pagehide', () => {
    controller?.abort();
    clearTimeout(timer);
  });
})();
