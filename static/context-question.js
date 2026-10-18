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
      const u = new URL(url, location.href);
      if (['http:', 'https:'].includes(u.protocol)) {
        a.href = u.href;
        a.rel = 'noopener noreferrer';
      }
    } catch (_) {}
    return a;
  }
  function explanationParagraph(text, references) {
    const paragraph = el('p');
    let cursor = 0;
    for (const match of text.matchAll(/\[([^\]\n]+)\]/g)) {
      const ids = [...new Set(match[1].split(',').map((id) => id.trim()))];
      if (!ids.length || !ids.every((id) => references.has(id))) continue;
      paragraph.append(document.createTextNode(text.slice(cursor, match.index)));
      const citations = el('span', null, 'answer-citations');
      for (const id of ids) {
        const source = references.get(id);
        const link = safeLink('[' + source.number + ']', '#answer-source-' + source.number);
        link.title = source.title || '원자료';
        link.setAttribute('aria-label', '인용 ' + source.number + ' · ' + link.title);
        citations.append(link);
      }
      paragraph.append(citations);
      cursor = match.index + match[0].length;
    }
    paragraph.append(document.createTextNode(text.slice(cursor)));
    return paragraph;
  }
  function evidenceSummary(data, references, complete) {
    const info = data.retrieval || {};
    const box = el('section', null, 'evidence-summary');
    box.setAttribute('aria-label', '답변의 자료 활용 범위');
    const counts = el('dl', null, 'evidence-counts');
    const cited = new Set(
      (data.claims || []).flatMap((c) => c.evidence_ids || []).filter((id) => references.has(id))
    );
    const number = (value, unit) =>
      Number.isInteger(value) ? `${value.toLocaleString()}${unit}` : '기록 없음';
    for (const [label, value] of [
      ['검색 후보', number(info.candidate_documents, '개 문서')],
      ['선정 자료', number(info.unique_documents, '개 문서')],
      ['근거 발췌', `${references.size.toLocaleString()}개`],
      ['실제 인용', complete ? `${cited.size.toLocaleString()}개 발췌` : '검토 전'],
    ]) {
      const item = el('div');
      item.append(el('dt', label), el('dd', value));
      counts.append(item);
    }
    box.append(counts);
    const details = el('details');
    details.append(el('summary', '선정 범위와 쟁점별 검색 현황'));
    details.append(
      el(
        'p',
        '검색 후보는 관련도 선별 후 모은 문서입니다. 동일 자료의 여러 발췌를 문서 수에 더하지 않으며, 저장 자료 전체를 전수 독해한 결과는 아닙니다.',
        'legend'
      )
    );
    if (
      Number.isInteger(info.initial_candidate_documents) &&
      Number.isInteger(info.supplemental_candidate_documents)
    )
      details.append(
        el(
          'p',
          `1차 검색 후보 ${info.initial_candidate_documents}개 · 원문 보강에서 추가 확인한 후보 ${info.supplemental_candidate_documents}개. 같은 문서는 중복 집계하지 않습니다.`,
          'legend'
        )
      );
    if (Number.isInteger(info.searched_source_records))
      details.append(
        el(
          'p',
          `색인에서 조회한 자료 기록 ${info.searched_source_records.toLocaleString()}건 · 동일 문서의 여러 분석 기록이 포함될 수 있습니다.`,
          'legend'
        )
      );
    if (complete && Number.isInteger(info.cited_documents))
      details.append(
        el(
          'p',
          `답변에서 실제 인용한 고유 자료 ${info.cited_documents}개 · 인용 발췌 ${cited.size}개`,
          'legend'
        )
      );
    if (info.budget) {
      details.append(
        el(
          'p',
          `질문별 상한: 고유 자료 ${info.budget.documents}개 · 발췌 ${info.budget.passages}개. 필요한 쟁점과 조건·반대 단서, 자료 유형을 보강합니다.`,
          'legend'
        )
      );
      if (Number.isInteger(info.candidate_limit_per_query))
        details.append(
          el(
            'p',
            `세부 검색어마다 최대 ${info.candidate_limit_per_query}개 후보 문서를 살핀 뒤 위 상한 안에서 선정합니다. 후보 전체가 답변 모델에 전달되는 것은 아닙니다.`,
            'legend'
          )
        );
      if (info.reserved_documents > 0)
        details.append(
          el(
            'p',
            `조건·반대 단서와 자료 유형을 보강하기 위해 ${info.reserved_documents}개 문서를 우선 배정했습니다. 실제 상충 여부는 독립 검토에서 확인합니다.`,
            'legend'
          )
        );
      if (info.budget_limited)
        details.append(
          el(
            'p',
            '자료 수 또는 원문 분량 상한으로 일부 후보를 제외했습니다. 선택된 근거 범위에서 답변합니다.',
            'legend'
          )
        );
    }
    if (info.axes?.length) {
      const list = el('ul', null, 'evidence-axes');
      for (const axis of info.axes) {
        const state =
          {
            no_candidates: '검색 후보 없음',
            not_selected: '선정 근거 없음',
            below_target: '보강 목표 미달',
            target_met: '검색 배정 목표 충족',
          }[axis.status] || '검색 현황';
        list.append(
          el(
            'li',
            `${axis.axis}: 후보 ${axis.candidate_documents}개 / 선정 ${axis.selected_documents}개 · ${state}`
          )
        );
      }
      details.append(
        list,
        el(
          'p',
          '위 수치는 표현 일치에 따른 검색 배정 현황입니다. 근거의 충분성과 주장 지지 여부는 독립 검토에서 별도로 판단합니다.',
          'legend'
        )
      );
    }
    box.append(details);
    return box;
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
    if (data.workspace_as_of && data.source_scope_note) {
      const asOf = new Date(data.workspace_as_of * 1000).toLocaleString('ko-KR');
      root.append(el('p', `자료 준비 ${asOf} · ${data.source_scope_note}`, 'legend'));
    }
    const reviewed = complete && data.verification?.method === 'independent_evidence_review';
    const references = new Map(
      (data.evidence || []).map((e, i) => [e.id, { ...e, number: i + 1 }])
    );
    if (references.size || data.retrieval?.policy)
      root.append(evidenceSummary(data, references, complete));
    const quality = data.verification?.quality;
    const incomplete = quality?.status === 'needs_review';
    if (reviewed) {
      root.append(el('h3', incomplete ? '종합 설명 보완 필요' : '질문에 대한 종합 설명'));
      if (quality) {
        const status = el('div', null, 'answer-quality');
        status.dataset.status = quality.status;
        status.append(
          el(
            'strong',
            incomplete
              ? '설명 충실도 검토 미완료'
              : quality.revision_count
                ? '보완 후 재검토 완료'
                : '질문 적합성·설명 충실도 확인'
          )
        );
        if (quality.status === 'evidence_limited')
          status.append(
            el(
              'p',
              '확보한 근거 범위의 답변입니다. 확인할 수 없는 쟁점과 판단의 한계를 함께 설명합니다.'
            )
          );
        if (incomplete) for (const issue of quality.issues || []) status.append(el('p', issue));
        root.append(status);
      }
      const explanation = el('div', null, 'answer-synthesis');
      for (const paragraph of (data.answer || '').split(/\n\s*\n/).filter(Boolean))
        explanation.append(explanationParagraph(paragraph, references));
      root.append(explanation);
      if (data.analysis_plan?.axes?.length)
        root.append(el('p', '검토한 축 · ' + data.analysis_plan.axes.join(' / '), 'legend'));
    } else if (complete)
      root.append(el('p', data.answer || '현재 확보한 자료만으로는 질문에 답하기 어렵습니다.'));
    if (reviewed) {
      const support = el('details', null, 'answer-support');
      support.open = incomplete;
      support.append(
        el('summary', `검토한 근거·관계·통찰 ${(data.claims || []).length}건 자세히 보기`)
      );
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
        support.append(section);
      }
      root.append(support);
    }
    for (const text of data.limitations || [])
      if (!(data.answer || '').includes(text)) root.append(el('p', text, 'legend'));
    const sources = el('details');
    sources.open = !complete;
    sources.append(el('summary', `검토 대상 근거 발췌 ${references.size}개 · 출처와 원문 확인`));
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
        if (stage.id === 'plan' && data.planning_note) {
          item.append(el('br'), el('small', data.planning_note));
        }
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
      : data.total > 0 && data.completed === data.total
        ? '검토 완료 · 결과와 최신 근거 확인 중'
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
      if (job.status === 'needs_review') {
        render(job.result, true);
        progress(
          '',
          job.error || '질문에 대한 설명을 더 보완해야 합니다.',
          '근거 검토를 통과한 부분과 남은 쟁점을 확인할 수 있습니다.'
        );
        finish();
        $('#answer-retry').hidden = !lastQuestion;
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
