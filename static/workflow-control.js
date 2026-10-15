/* Read-only evidence/decision control room. Motion follows recorded events. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const ns = 'http://www.w3.org/2000/svg';
  const states = {
    running: '진행',
    complete: '완료 기록',
    queued: '대기',
    preparing: '준비',
    planned: '준비',
    failed: '실패',
    needs_review: '검토 필요',
    stale: '입력 변경',
    interrupted: '중단',
    owner_missing: '실행자 없음',
    skipped: '비교 생략',
    busy: '사용 중',
    cooldown: '재시도 대기',
    unavailable: '연결 오류',
    authentication: '인증 확인',
    rate_limit: '한도 대기',
    invalid_response: '응답 확인',
    invalid_input: '입력 확인',
    storage_unavailable: '저장 지연',
    pending: '기록 없음',
    paused: '일시중지',
    evidence_only: '근거만 확보',
  };
  const names = {
    collection: '입력 확인',
    enrichment: '원문 보강',
    source_repair: '원문 재확보',
    execution_plan: '처리 경로',
    morphology: '핵심 표현',
    graph_retrieval: '관계 검색',
    strategy_draft: '전략 초안',
    integrated_analysis: '통합 분석',
    risk_assessment: '위험 분석',
    national: '국가 관점',
    technology: '기술 관점',
    deliberation: '쟁점 정리',
    synthesis: '종합 작성',
    integrated_verification: '독립 공동 검토',
    verification: '전략 검토',
    risk_verification: '위험 검토',
    revision: '전략 보완',
    risk_revision: '위험 보완',
    reverification: '전략 재검토',
    risk_reverification: '위험 재검토',
    integrated_reverification: '공동 재검토',
    final: '결과 저장',
    finalize: '최신 근거 확인',
    evidence: '검색 준비',
    plan: '질문 계획',
    review: '답변 검토',
    review_repair: '답변 설명 보완',
    review_recheck: '답변 독립 재검토',
    review_incomplete: '답변 검토 미완료',
    jev: 'Jev 비교 평가',
    wiki: '위키 편찬',
    strategy_audit_reuse: '검토 재사용',
  };
  const choices = {
    research: '연구',
    announcement: '출시 안내',
    policy: '정책·전략',
    unknown: '판단 유보',
    required: '상세 위험 검토 필요',
    unclear: '불확실',
    routine: '일반',
    present: '상충·조건 있음',
    absent: '제시 자료에 상충 없음',
    compact: '통합 작성 경로',
    parallel: '병렬 작성 경로',
    full: '전체 역할 경로',
    direct: '직접 근거',
    counter: '상충·한계 근거',
    context: '배경 근거',
    unrelated: '관련성 낮음',
    entity: '대상',
    concept: '개념',
    event: '사건',
    claim: '주장',
    question: '질문',
  };
  const reasons = {
    pending_limit: '비교 작업 동시 한도',
    deterministic_compact: '이미 규칙으로 최소 경로 적용',
    route_out_of_scope: '비교 대상 경로 밖',
    input_limit: '입력 크기 상한',
    candidate_limit: '근거 후보 수 상한',
  };
  const kinds = { workflow: '기사 분석', question: '종합 질의', wiki: '위키 편찬' };
  const status = (v) => states[v] || v || '기록 없음';
  const number = (v) => (v == null ? '—' : Number(v).toLocaleString('ko-KR'));
  const time = (v) =>
    v
      ? new Date(v * 1000).toLocaleString('ko-KR', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: false,
        })
      : '시각 없음';
  const shortTime = (v) => new Date(v * 1000).toLocaleTimeString('ko-KR', { hour12: false });
  const el = (tag, text, parent, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    parent?.append(n);
    return n;
  };
  const svg = (tag, attrs, parent, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    if (text != null) n.textContent = text;
    parent?.append(n);
    return n;
  };
  const safeURL = (value) => {
    try {
      const u = new URL(value, location.origin);
      return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : null;
    } catch {
      return null;
    }
  };
  const active = (s) => ['running', 'queued', 'preparing', 'planned'].includes(s);
  const attention = (s) =>
    [
      'failed',
      'needs_review',
      'stale',
      'interrupted',
      'owner_missing',
      'authentication',
      'invalid_response',
    ].includes(s);
  const groupFor = (s) =>
    ['collection', 'enrichment', 'source_repair', 'evidence'].includes(s)
      ? 0
      : s === 'jev'
        ? 1
        : ['execution_plan', 'plan', 'morphology', 'graph_retrieval'].includes(s)
          ? 2
          : /verification|review|revision|audit/.test(s)
            ? 4
            : ['final', 'finalize', 'wiki'].includes(s)
              ? 5
              : 3;
  let latest = null,
    selected = new URLSearchParams(location.search).get('task') || '',
    detail = null,
    detailVersion = '',
    view = new URLSearchParams(location.search).get('view') === 'board' ? 'board' : 'control';
  let cursor = null,
    playing = null,
    selectedNode = null,
    controller = null,
    requestId = 0,
    closed = false,
    offline = true,
    lastEvent = null,
    following =
      new URLSearchParams(location.search).get('follow') !== '0' &&
      (new URLSearchParams(location.search).get('follow') === '1' || !selected),
    detailLoading = false,
    detailFresh = false,
    detailSignature = '';
  let zoom = 1,
    panX = 0,
    panY = 0,
    drag = null,
    dragMoved = false;
  function theme(value) {
    document.body.dataset.labTheme = value === 'dark' ? 'dark' : 'light';
    $('lab-light').setAttribute('aria-pressed', String(value !== 'dark'));
    $('lab-dark').setAttribute('aria-pressed', String(value === 'dark'));
    try {
      localStorage.setItem('news-workflow-theme', value);
    } catch {
      /* Storage can be disabled. */
    }
  }
  let storedTheme = 'light';
  try {
    storedTheme = localStorage.getItem('news-workflow-theme') || 'light';
  } catch {
    /* Use the default. */
  }
  theme(storedTheme);
  $('lab-light').onclick = () => theme('light');
  $('lab-dark').onclick = () => theme('dark');
  function setZoom(value) {
    zoom = Math.max(1, Math.min(3, value));
    const w = 1200 / zoom,
      h = 560 / zoom;
    panX = Math.max(-(1200 - w) / 2, Math.min((1200 - w) / 2, panX));
    panY = Math.max(-(560 - h) / 2, Math.min((560 - h) / 2, panY));
    $('control-graph').setAttribute(
      'viewBox',
      `${(1200 - w) / 2 + panX} ${(560 - h) / 2 + panY} ${w} ${h}`
    );
    $('control-graph').dataset.zoomed = String(zoom > 1);
    $('lab-zoom').textContent = Math.round(zoom * 100) + '%';
    $('lab-zoom-out').disabled = zoom === 1;
    $('lab-zoom-in').disabled = zoom === 3;
  }
  function focusCanvas(value) {
    $('control-room').dataset.focus = String(value);
    $('lab-focus').setAttribute('aria-pressed', String(value));
    $('lab-focus').textContent = value ? '전체 보기 ↙' : '집중 보기 ⤢';
  }
  $('lab-focus').onclick = () => focusCanvas($('control-room').dataset.focus !== 'true');
  $('lab-zoom-in').onclick = () => setZoom(zoom + 0.5);
  $('lab-zoom-out').onclick = () => setZoom(zoom - 0.5);
  $('lab-fit').onclick = () => setZoom(1);
  $('lab-detail').onclick = () => {
    focusCanvas(false);
    $('control-inspector').scrollIntoView({ block: 'center' });
    $('control-inspector').focus({ preventScroll: true });
  };
  setZoom(1);
  const viewport = $('lab-viewport');
  if (matchMedia('(max-width: 760px)').matches) viewport.scrollLeft = 210;
  const canvas = $('control-graph');
  canvas.addEventListener('pointerdown', (e) => {
    dragMoved = false;
    if (zoom <= 1 || e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, panX, panY };
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    if (!dragMoved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
    dragMoved = true;
    canvas.setPointerCapture(e.pointerId);
    canvas.dataset.dragging = 'true';
    const matrix = canvas.getScreenCTM();
    if (!matrix) return;
    panX = drag.panX - (e.clientX - drag.x) / matrix.a;
    panY = drag.panY - (e.clientY - drag.y) / matrix.d;
    setZoom(zoom);
  });
  const endDrag = (e) => {
    drag = null;
    canvas.dataset.dragging = 'false';
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    // Keep the drag guard through the synthetic click, but allow later keyboard selection.
    setTimeout(() => {
      dragMoved = false;
    }, 0);
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('keydown', (e) => {
    const directions = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    if (e.key === 'Escape') {
      e.preventDefault();
      setZoom(1);
      focusCanvas(false);
    } else if (zoom > 1 && directions[e.key]) {
      e.preventDefault();
      panX += (directions[e.key][0] * 45) / zoom;
      panY += (directions[e.key][1] * 45) / zoom;
      setZoom(zoom);
    }
  });
  function follow(value) {
    following = value;
    $('control-follow').setAttribute('aria-pressed', String(value));
    $('control-follow').textContent = value
      ? '자동 따라가기 켜짐'
      : '선택 작업 고정 · 자동 따라가기';
    const url = new URL(location.href);
    url.searchParams.set('follow', value ? '1' : '0');
    history.replaceState(null, '', url);
  }
  function motion() {
    const stopped = offline || document.hidden || view !== 'control';
    $('control-room').dataset.motion = stopped || cursor !== null || !detailFresh ? 'off' : 'on';
    $('control-room').dataset.pipelineMotion = stopped ? 'off' : 'on';
    window.WorkflowAutomation?.connection(stopped);
  }
  $('control-follow').onclick = () => {
    follow(!following);
    if (following) {
      cursor = null;
      if (latest) update(latest);
    }
  };
  follow(following);
  function mode(value) {
    view = value;
    document.body.dataset.labView = value;
    $('flow-board').hidden = value !== 'board';
    $('control-room').hidden = value !== 'control';
    $('view-control').setAttribute('aria-pressed', String(value === 'control'));
    $('view-board').setAttribute('aria-pressed', String(value === 'board'));
    if (value === 'board') stop();
    else if (latest) update(latest);
    motion();
  }
  $('view-control').onclick = () => mode('control');
  $('view-board').onclick = () => mode('board');
  mode(view);
  function stop() {
    clearInterval(playing);
    playing = null;
    $('control-play').textContent = '▶ 기록 재생';
    $('control-play').setAttribute('aria-pressed', 'false');
  }
  function selectedAt() {
    if (cursor === null) return Infinity;
    return detail?.events.find((e) => e.id === cursor)?.at ?? -Infinity;
  }
  function displayEvents() {
    const events = detail?.events || [];
    return cursor === null ? events : events.slice(0, events.findIndex((e) => e.id === cursor) + 1);
  }
  function gotoIndex(index) {
    follow(false);
    stop();
    const events = detail?.events || [];
    cursor = events[index]?.id || null;
    renderDetail();
  }
  function visibleStatus(event) {
    if (!event) return 'pending';
    if (cursor !== null || event.status !== 'running') return event.status;
    if (event.stage === 'jev') {
      const observation = detail.decisions.find((d) => event.id === 'jev:' + d.id + ':start');
      return observation?.status === 'owner_missing' ? 'owner_missing' : event.status;
    }
    return active(detail.job.status)
      ? event.status
      : detail.job.status === 'owner_missing'
        ? 'owner_missing'
        : 'interrupted';
  }
  $('control-time').oninput = (event) => gotoIndex(Number(event.target.value));
  $('control-live').onclick = () => {
    stop();
    cursor = null;
    renderDetail();
  };
  $('control-play').onclick = () => {
    if (playing) {
      stop();
      return;
    }
    const events = detail?.events || [];
    if (events.length < 2) return;
    follow(false);
    if (cursor === null || cursor === events.at(-1).id) cursor = events[0].id;
    $('control-play').textContent = 'Ⅱ 재생 멈춤';
    $('control-play').setAttribute('aria-pressed', 'true');
    renderDetail();
    playing = setInterval(() => {
      const i = detail.events.findIndex((e) => e.id === cursor) + 1;
      if (i >= detail.events.length) {
        stop();
        return;
      }
      cursor = detail.events[i].id;
      renderDetail();
    }, 850);
  };
  function select(key, automatic = false) {
    if (!automatic) follow(false);
    if (key === selected && detail) return;
    stop();
    controller?.abort();
    requestId++;
    detailLoading = false;
    detailFresh = false;
    detailSignature = '';
    selected = key;
    detail = null;
    detailVersion = '';
    cursor = null;
    selectedNode = null;
    lastEvent = null;
    setZoom(1);
    const url = new URL(location.href);
    url.searchParams.set('task', key);
    url.searchParams.set('view', 'control');
    history.replaceState(null, '', url);
    $('control-title').textContent = '작업 이력을 불러오고 있습니다';
    $('control-inspector').textContent = '기록을 읽고 있습니다.';
    $('control-graph-content').replaceChildren();
    $('control-events').replaceChildren();
    $('control-graph-empty').hidden = false;
    $('control-graph-empty').textContent = '선택한 작업을 불러오고 있습니다.';
    $('control-lanes').replaceChildren();
    $('control-signals').replaceChildren();
    $('control-progress').replaceChildren();
    motion();
    $('control-result').hidden = true;
    $('control-time').disabled = true;
    $('control-play').disabled = true;
    renderJobs();
    fetchDetail();
  }
  function renderJobs() {
    const root = $('control-jobs');
    const focus = document.activeElement?.dataset.job;
    const scroll = root.scrollTop;
    root.replaceChildren();
    const q = $('control-search').value.toLocaleLowerCase(),
      filter = $('control-filter').value;
    const jobs = (latest?.control?.jobs || []).filter(
      (j) =>
        (j.title + ' ' + j.id).toLocaleLowerCase().includes(q) &&
        (filter === 'all' ||
          (filter === 'active' && active(j.status)) ||
          (filter === 'attention' && attention(j.status)) ||
          (filter === 'jev' && j.decision))
    );
    $('control-job-count').textContent = number(jobs.length) + '개';
    for (const job of jobs) {
      const button = el('button', null, root, 'control-job');
      button.type = 'button';
      button.dataset.job = job.key;
      button.setAttribute('aria-pressed', String(job.key === selected));
      el('strong', job.title, button);
      el(
        'small',
        `${kinds[job.kind]} · ${status(job.status)}${job.decision ? ' · Jev ' + status(job.decision.status) : ' · Jev 기록 없음'}`,
        button,
        'control-job-status'
      );
      el('small', time(job.updated_at), button);
      const p = job.progress;
      if (p?.total) el('small', `기록 단계 ${p.completed} / ${p.total} · ${p.percent}%`, button);
      if (p?.active?.length)
        el(
          'small',
          p.active.map((s) => names[s.stage] || s.stage).join(' · '),
          button,
          'control-job-active'
        );
      button.onclick = () => select(job.key);
      if (focus === job.key) button.focus({ preventScroll: true });
    }
    if (!jobs.length)
      el('p', '표시 범위에 조건과 일치하는 작업이 없습니다.', root, 'control-caption');
    root.scrollTop = scroll;
  }
  $('control-search').oninput = renderJobs;
  $('control-filter').onchange = renderJobs;
  function metrics(data) {
    const control = data.control || {},
      m = control.metrics || {},
      root = $('control-metrics');
    root.replaceChildren();
    const rows = [
      [
        '실행 중인 모델',
        number(data.call_counts?.running) + ' / ' + number(data.call_limit),
        '공유 모델 · 대기 ' + number(data.call_counts?.queued) + '개',
      ],
      ['Jev 실제 완료', number(m.calls), '최근 ' + (control.window || 80) + '개 비교 기록 중'],
      [
        'Jev 처리시간',
        m.p50_ms == null ? '—' : number(Math.round(m.p50_ms)) + ' ms',
        '비캐시 완료 중앙값',
      ],
      ['Jev 입력 토큰', number(m.input_tokens), '같은 범위 · 캐시 제외'],
      ['재사용 / 생략', number(m.cached) + ' / ' + number(m.skipped), '비교 평가 요청 기준'],
      [
        '경로 일치 / 비교',
        number(m.route_agreements) + ' / ' + number(m.route_compared),
        '기존 경로와 비교 · 정답률 아님',
      ],
    ];
    for (const [label, value, note] of rows) {
      const box = el('div', null, root, 'control-metric');
      el('span', label, box);
      el('strong', value, box);
      el('small', note, box);
    }
    const settings = control.settings || {};
    $('control-mode').textContent = settings.enabled
      ? 'JEV · 비교 평가'
      : settings.configured
        ? 'JEV · 중지'
        : 'JEV · 키 설정 대기';
  }
  async function fetchDetail() {
    if (!selected || view !== 'control' || closed || detailLoading) return;
    const colon = selected.indexOf(':'),
      kind = selected.slice(0, colon),
      id = selected.slice(colon + 1);
    if (colon < 1) return;
    controller?.abort();
    const token = ++requestId;
    detailLoading = true;
    controller = new AbortController();
    try {
      const data = await Workspace.request(
        '/api/operations/control?' + new URLSearchParams({ kind, id }),
        { timeout: 10000, signal: controller.signal }
      );
      if (token !== requestId || closed) return;
      detailFresh = true;
      const signature = JSON.stringify(data);
      if (detailSignature === signature) {
        motion();
        return;
      }
      detailSignature = signature;
      detail = data;
      if (cursor && !data.events.some((e) => e.id === cursor)) {
        stop();
        cursor = data.events[0]?.id || null;
      }
      $('control-message').textContent = data.history_note;
      renderDetail();
    } catch (error) {
      if (token !== requestId || closed || error.name === 'AbortError') return;
      detailVersion = '';
      detailFresh = false;
      motion();
      $('control-message').textContent =
        '작업 이력 연결이 지연됩니다. 마지막 기록을 유지하고 다시 확인합니다.';
      if (!detail)
        $('control-graph-empty').textContent =
          '작업 기록을 읽지 못했습니다. 다음 갱신에서 다시 확인합니다.';
    } finally {
      if (token === requestId) detailLoading = false;
    }
  }
  function update(data) {
    latest = data;
    if (view !== 'control') return;
    metrics(data);
    window.WorkflowAutomation?.update(data);
    renderJobs();
    if (!data.control) {
      $('control-message').textContent =
        '관제 이력 API를 준비 중입니다. 단계별 시황판은 계속 이용할 수 있습니다.';
      return;
    }
    if (data.control.unavailable) {
      $('control-message').textContent =
        '관제 기록 조회가 지연되고 있습니다. 다음 갱신에서 다시 확인합니다.';
      return;
    }
    if (following && cursor === null) {
      const current = data.control.jobs.find((j) => j.key === selected);
      const next =
        data.control.jobs.find((j) => ['running', 'preparing'].includes(j.status)) ||
        data.control.jobs.find((j) => ['queued', 'planned'].includes(j.status));
      if (next && !active(current?.status)) {
        select(next.key, true);
        return;
      }
    }
    if (!selected && data.control.jobs.length) {
      select(
        data.control.jobs.find((j) => j.decision)?.key ||
          data.control.jobs.find((j) => j.kind === 'workflow')?.key ||
          data.control.jobs[0].key,
        true
      );
      return;
    }
    if (!selected) {
      $('control-graph-empty').textContent =
        '아직 기록된 분석 작업이 없습니다. 새 분석의 실제 기록부터 표시합니다.';
      return;
    }
    const job = data.control.jobs.find((j) => j.key === selected),
      version = JSON.stringify([
        selected,
        job?.status,
        job?.updated_at,
        job?.decision,
        job?.progress?.revision,
      ]);
    if (
      version !== detailVersion ||
      active(job?.status) ||
      job?.decision?.status === 'running' ||
      !detail
    ) {
      detailVersion = version;
      fetchDetail();
    }
  }
  function node(parent, x, y, title, key, kind, subtitle = '') {
    const g = svg(
      'g',
      {
        class: 'control-node',
        tabindex: 0,
        role: 'button',
        'aria-label': title,
        'data-kind': kind,
        'data-selected': String(selectedNode?.key === key),
      },
      parent
    );
    const left = kind === 'source';
    svg(
      'rect',
      { x: x - (left ? 202 : 12), y: y - 11, width: 216, height: 26, rx: 2, class: 'lab-node-hit' },
      g
    );
    svg('circle', { cx: x, cy: y, r: 4 }, g);
    svg('title', {}, g, title);
    svg(
      'text',
      { x: x + (left ? -12 : 12), y: y - 1, 'text-anchor': left ? 'end' : 'start' },
      g,
      title.length > (left ? 19 : 14) ? title.slice(0, left ? 18 : 13) + '…' : title
    );
    svg(
      'text',
      {
        x: x + (left ? -12 : 12),
        y: y + 12,
        'text-anchor': left ? 'end' : 'start',
        class: 'lab-node-meta',
      },
      g,
      subtitle
    );
    const choose = () => {
      if (dragMoved) return;
      follow(false);
      selectedNode = { key, kind };
      renderDetail();
    };
    g.onclick = choose;
    g.onkeydown = (e) => {
      if (['Enter', ' '].includes(e.key)) {
        e.preventDefault();
        choose();
      }
    };
  }
  function graph() {
    const g = $('control-graph-content');
    const focus = document.activeElement?.getAttribute('aria-label');
    g.replaceChildren();
    $('control-graph-empty').hidden = true;
    const sources = (detail.sources || []).slice(0, 16),
      related = (detail.related || []).slice(0, 10),
      at = selectedAt();
    const observation = detail.decisions
      .filter((d) => d.finished_at && d.finished_at <= at && d.status === 'complete')
      .at(-1);
    const answers = Object.entries(observation?.result?.answers || {}).slice(0, 6);
    const events = displayEvents(),
      latestStages = new Map();
    events.forEach((e) => latestStages.set(e.stage, e));
    const runningStages = [...latestStages.values()].filter((e) => visibleStatus(e) === 'running');
    const center = runningStages.at(-1) || events.at(-1),
      changed = cursor === null && !offline && center && lastEvent && center.id !== lastEvent,
      executing = cursor === null && runningStages.length > 0;
    // Architectural grid, not synthetic evidence or inferred relationships.
    const grid = svg('g', { 'aria-hidden': 'true' }, g);
    for (const r of [170, 260, 350, 420])
      svg('circle', { cx: 28, cy: 280, r, class: 'lab-grid' }, grid);
    for (const rx of [80, 180, 285, 370])
      svg('ellipse', { cx: 28, cy: 280, rx, ry: 420, class: 'lab-grid' }, grid);
    for (const ry of [70, 180, 290])
      svg('ellipse', { cx: 28, cy: 280, rx: 420, ry, class: 'lab-grid' }, grid);
    svg('circle', { cx: -42, cy: 280, r: 500, class: 'lab-ring-band' }, grid);
    for (const r of [449, 454, 545, 551])
      svg('circle', { cx: -42, cy: 280, r, class: 'lab-ring' }, grid);
    const polar = (radius, angle) => [
      -42 + radius * Math.cos(angle),
      280 + radius * Math.sin(angle),
    ];
    for (let i = -36; i <= 36; i++) {
      const a = (i * Math.PI) / 180,
        [x, y] = polar(547, a),
        [xx, yy] = polar(i % 5 ? 553 : 560, a);
      svg('path', { d: `M${x} ${y}L${xx} ${yy}`, class: 'lab-tick' }, grid);
    }
    svg('text', { x: 28, y: 34, class: 'lab-field-title' }, g, '01 / EVIDENCE FIELD');
    svg(
      'text',
      { x: 28, y: 105, class: 'lab-count' },
      g,
      String(detail.sources.length).padStart(2, '0')
    );
    svg('text', { x: 28, y: 127 }, g, `저장 입력 근거 · 화면 ${sources.length}개`);
    svg(
      'text',
      { x: 28, y: 146, class: 'lab-node-meta' },
      g,
      `검색 단서 ${related.length} / ${detail.related.length}개`
    );
    svg('text', { x: 595, y: 34, class: 'lab-field-title' }, g, '02 / DECISION GATE');
    const points = new Map();
    for (const [i, n] of related.entries()) {
      const x = 35 + (i % 3) * 12,
        y = 200 + i * 30;
      points.set(n.id, [x, y]);
      svg('path', { d: `M${x} ${y}C310 ${y} 260 280 490 280`, class: 'control-edge hint' }, g);
    }
    for (const edge of detail.relations || []) {
      const a = points.get(edge.source),
        b = points.get(edge.target);
      if (!a || !b) continue;
      const line = svg(
        'path',
        { d: `M${a[0]} ${a[1]}Q280 280 ${b[0]} ${b[1]}`, class: 'control-edge hint' },
        g
      );
      svg('title', {}, line, '당시 검색 관계 · ' + (edge.relation || edge.type || '연결'));
    }
    for (const [i, s] of sources.entries()) {
      // Two arcs leave the decision gate readable even with the maximum input count.
      const half = Math.ceil(sources.length / 2),
        top = i < half,
        slot = top ? i : i - half,
        count = top ? half : sources.length - half,
        degrees = top
          ? -28 + (slot * 24) / Math.max(1, count - 1)
          : 4 + (slot * 24) / Math.max(1, count - 1),
        [x, y] = polar(500, (degrees * Math.PI) / 180),
        key = 'source:' + i;
      svg(
        'path',
        {
          d: `M${x} ${y}C${x + 100} ${y} 420 280 510 280`,
          class:
            'control-edge' +
            (selectedNode?.key === key ? ' selected' : '') +
            (executing ? ' control-stream' : '') +
            (changed ? ' control-pulse' : ''),
        },
        g
      );
      node(
        g,
        x,
        y,
        s.title || s.id,
        key,
        'source',
        `${String(i + 1).padStart(2, '0')} / ${
          {
            stored_news_excerpt: '저장 뉴스',
            telegram_excerpt: 'Telegram 발췌',
            arxiv_abstract: '공식 논문 초록',
            fetched_url_excerpt: '확보 원문 발췌',
            news: '뉴스',
            retrieval_node: '검색 근거',
            paper: '논문',
            external: '외부 원문',
          }[s.kind] || '저장 근거'
        }`
      );
    }
    for (const [i, n] of related.entries()) {
      const [x, y] = points.get(n.id);
      node(g, x, y, n.name || n.id, 'related:' + i, 'related', n.type || '검색 단서');
    }
    // One trace per stored event; no invented signal density or probability values.
    const traces = events.slice(-12),
      first = traces[0]?.at,
      last = traces.at(-1)?.at;
    const traceTop = answers.length ? 355 : 350;
    svg('text', { x: 760, y: traceTop - 19, class: 'lab-field-title' }, g, '03 / EVENT TRACE');
    svg(
      'text',
      { x: 1168, y: traceTop - 19, 'text-anchor': 'end', class: 'lab-node-meta' },
      g,
      '저장된 단계 발생 시점'
    );
    traces.forEach((e, i) => {
      const y = traceTop + i * 14,
        phase = e.status,
        x = 995 + (last > first ? (e.at - first) / (last - first) : 1) * 170;
      svg(
        'path',
        {
          d: `M560 ${205 + i * 13}C650 ${205 + i * 13} 665 ${y} 746 ${y}`,
          class: 'lab-record-link',
        },
        g
      );
      svg(
        'text',
        { x: 760, y: y + 3, class: 'lab-trace-time' },
        g,
        `${shortTime(e.at)} / ${(names[e.stage] || e.stage).slice(0, 12)}`
      );
      svg('path', { d: `M995 ${y}H1170`, class: 'lab-trace-line' }, g);
      const mark = svg(
        'rect',
        {
          x,
          y: y - 3,
          width: 4,
          height: 6,
          class: 'lab-event-mark',
          'data-attention': String(attention(phase)),
        },
        g
      );
      svg('title', {}, mark, `${names[e.stage] || e.stage} · ${status(phase)} · ${time(e.at)}`);
    });
    if (!traces.length)
      svg('text', { x: 760, y: traceTop + 8 }, g, '이 시점에 저장된 단계 기록 없음');
    // The aperture is a visual stage marker, not an additional agent/model.
    svg('path', { d: 'M510 228L555 254V306L510 332L465 306V254Z', class: 'lab-hub' }, g);
    svg('path', { d: 'M510 247L538 296H482Z', class: 'lab-aperture' }, g);
    svg('circle', { cx: 510, cy: 280, r: 9, class: 'lab-aperture-core' }, g);
    svg(
      'circle',
      {
        cx: 510,
        cy: 280,
        r: 62,
        class: 'lab-ring' + (executing ? ' control-orbit' : ''),
        'stroke-dasharray': '2 8',
      },
      g
    );
    svg('path', { d: 'M573 280H730', class: 'control-edge' }, g);
    svg(
      'text',
      { x: 600, y: 237, class: 'control-core-sub' },
      g,
      cursor === null ? 'CURRENT CHECKPOINT' : 'RECORDED CHECKPOINT'
    );
    svg(
      'text',
      { x: 600, y: 265, class: 'control-core-title' },
      g,
      runningStages.length > 1
        ? runningStages.length + '개 단계 병행'
        : center
          ? names[center.stage] || center.stage
          : '기록 대기'
    );
    svg(
      'text',
      { x: 600, y: 302, class: 'control-core-sub' },
      g,
      center ? status(visibleStatus(center)) : '관측된 작업만 표시'
    );
    if (answers.length) {
      answers.forEach(([key, a], i) => {
        const x = 790,
          y = 63 + i * 40,
          title = questionName(key),
          value = choices[a.choice] || a.choice || '예 판단값 ' + a.noul;
        svg(
          'path',
          { d: `M555 280C690 280 680 ${y + 16} ${x} ${y + 16}`, class: 'control-edge hint' },
          g
        );
        const b = svg(
          'g',
          {
            class: 'lab-answer',
            tabindex: 0,
            role: 'button',
            'aria-label': title + ' · ' + value,
            'data-selected': String(selectedNode?.key === 'answer:' + key),
            'data-attention': String(['required', 'present', 'unclear'].includes(a.choice)),
          },
          g
        );
        svg('rect', { x, y, width: 380, height: 34 }, b);
        svg('text', { x: x + 10, y: y + 13, class: 'lab-answer-label' }, b, title);
        svg(
          'text',
          { x: x + 10, y: y + 27, class: 'lab-answer-value' },
          b,
          String(value).slice(0, 42)
        );
        const choose = () => {
          if (dragMoved) return;
          follow(false);
          selectedNode = { key: 'answer:' + key, kind: 'decision' };
          renderDetail();
        };
        b.onclick = choose;
        b.onkeydown = (e) => {
          if (['Enter', ' '].includes(e.key)) {
            e.preventDefault();
            choose();
          }
        };
      });
    } else {
      svg('text', { x: 790, y: 100, class: 'lab-output-title' }, g, '판단 기록 대기');
      svg('text', { x: 790, y: 128 }, g, '이 시점의 Jev 완료 판단 없음');
      svg('text', { x: 790, y: 149 }, g, '저장된 작업 이력은 아래에 표시됩니다');
    }
    svg('text', { x: 28, y: 550, class: 'lab-field-title' }, g, 'SAVED INPUTS / RETRIEVAL CONTEXT');
    svg(
      'text',
      { x: 1168, y: 550, 'text-anchor': 'end', class: 'lab-field-title' },
      g,
      'JEV SHADOW · INDEPENDENT REVIEW'
    );
    if (cursor === null) lastEvent = center?.id;
    if (focus)
      Array.from(g.querySelectorAll('[role="button"]'))
        .find((n) => n.getAttribute('aria-label') === focus)
        ?.focus({ preventScroll: true });
  }
  function lanes() {
    const root = $('control-lanes');
    root.replaceChildren();
    const stages = new Map();
    displayEvents().forEach((e) => stages.set(e.stage, e));
    ['원문·입력', 'Jev 비교', '경로·검색', '분석 작성', '독립 검토', '확인·저장'].forEach(
      (title, i) => {
        const group = [...stages.values()].filter((e) => groupFor(e.stage) === i);
        const e =
          group.find((e) => visibleStatus(e) === 'running') ||
          group.find((e) => attention(visibleStatus(e))) ||
          group.at(-1);
        const state = visibleStatus(e);
        const box = el('div', title, root, 'control-lane');
        box.dataset.status = state;
        el('small', status(state), box);
      }
    );
  }
  function signals() {
    const root = $('control-signals');
    root.replaceChildren();
    const d = detail.decisions
      .filter((d) => d.finished_at && d.finished_at <= selectedAt() && d.status === 'complete')
      .at(-1);
    for (const [key, a] of Object.entries(d?.result.answers || {}).slice(0, 8)) {
      const b = el(
        'button',
        questionName(key) + ' · ' + (choices[a.choice] || a.choice || '예 판단값 ' + a.noul),
        root,
        'control-signal'
      );
      b.setAttribute('aria-pressed', String(selectedNode?.key === 'answer:' + key));
      b.onclick = () => {
        follow(false);
        selectedNode =
          selectedNode?.key === 'answer:' + key ? null : { kind: 'decision', key: 'answer:' + key };
        renderDetail();
      };
    }
    if (!d) el('span', '이 시점의 Jev 완료 판단 없음', root, 'control-caption');
  }
  function questionName(key) {
    return (
      {
        content: '자료 유형',
        risk: '위험 검토 필요성',
        contradictions: '상충·조건',
        route: '처리 경로 제안',
      }[key] || key.replace('candidate_', '근거 후보 ').replace('note_', '인용 후보 ')
    );
  }
  function inspector() {
    const root = $('control-inspector'),
      scroll = root.scrollTop;
    root.replaceChildren();
    const at = selectedAt();
    if (selectedNode?.kind === 'source') {
      const source = detail.sources[Number(selectedNode.key.split(':')[1])];
      if (source) {
        el('h3', source.title, root);
        el(
          'p',
          '이 작업에 사용된 저장 입력 근거입니다. 출처의 주장과 검토된 해석을 구분합니다.',
          root
        );
        el('small', source.kind + ' · ' + source.id, root);
        const url = safeURL(source.url);
        if (url) {
          const a = el('a', '원출처 열기 ↗', root);
          a.href = url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
        } else el('p', '이 기록에는 원출처 주소가 없습니다.', root);
      }
    } else if (selectedNode?.kind === 'related') {
      const n = detail.related[Number(selectedNode.key.split(':')[1])];
      if (n) {
        el('h3', n.name, root);
        el('p', `${n.type} · ${n.id}`, root);
        el(
          'p',
          '당시 관련 근거 검색에서 찾은 탐색 단서입니다. 이 연결만으로 인과관계나 현재 유효한 결론을 확정하지 않습니다.',
          root
        );
        const a = el('a', '통합 분석실에서 탐색 ↗', root);
        a.href = '/context?' + new URLSearchParams({ q: n.name || n.id });
      }
    } else {
      const visible = detail.decisions.filter((d) => d.started_at <= at);
      const d = visible.at(-1);
      if (!d) {
        el(
          'p',
          cursor === null
            ? '이 작업에 연결된 Jev 비교 기록이 없습니다. 과거 판단을 새로 만들어 표시하지 않습니다.'
            : '이 시점에는 Jev 판단 기록이 없습니다.',
          root
        );
      } else {
        const finished = d.finished_at && d.finished_at <= at;
        el(
          'p',
          `${d.model} · ${finished || (cursor === null && !active(d.status)) ? status(d.status) : '평가 요청 처리 중'}${finished && d.cached ? ' · 정확 입력 캐시' : ''}`,
          root
        );
        if (d.context.historical_comparison)
          el(
            'p',
            '과거 저장 근거에 대한 새 비교 평가입니다. 원래 분석 당시의 판단이 아닙니다.',
            root
          );
        el('small', '입력 해시 ' + d.input_hash.slice(0, 16), root);
        if (finished) {
          const u = d.result.usage || {};
          el(
            'div',
            `${d.cached ? '캐시 재사용 · 추가 API 호출 없음' : `실측 ${number(d.elapsed_ms)} ms · 입력 ${number(u.input_tokens)} / 출력 ${number(u.output_tokens)} 토큰`}`,
            root,
            'control-token-row'
          );
          if (d.result.reason) el('p', reasons[d.result.reason] || status(d.result.reason), root);
          if (d.result.retry_at) el('p', '재시도 가능 시각 ' + time(d.result.retry_at), root);
          for (const [key, a] of Object.entries(d.result.answers || {})) {
            if (selectedNode?.kind === 'decision' && selectedNode.key !== 'answer:' + key) continue;
            const box = el('article', null, root, 'control-decision');
            el('h3', questionName(key), box);
            el(
              'div',
              choices[a.choice] ||
                a.choice ||
                (a.noul != null ? '예 판단값 ' + a.noul : '응답 없음'),
              box,
              'control-choice'
            );
            if (a.confidence != null)
              el(
                'p',
                '모델 확신도 ' + Math.round(a.confidence * 100) + '% · 사건 발생 확률 아님',
                box
              );
            if (a.probabilities) {
              const bar = el('div', null, box, 'control-probabilities');
              for (const [label, p] of Object.entries(a.probabilities)) {
                const part = el('span', null, bar);
                part.style.width = p * 100 + '%';
                part.title = (choices[label] || label) + ' ' + (p * 100).toFixed(1) + '%';
              }
              el(
                'p',
                Object.entries(a.probabilities)
                  .map(([label, p]) => (choices[label] || label) + ' ' + (p * 100).toFixed(1) + '%')
                  .join(' · '),
                box
              );
            }
            const rubric = el('details', null, box);
            el('summary', '판단 질문과 기준', rubric);
            el('p', d.questions[key]?.instructions, rubric);
            for (const [k, v] of Object.entries(d.questions[key]?.criteria || {}))
              el(
                'p',
                `${choices[k] || k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`,
                rubric
              );
          }
          if (d.baseline.path)
            el(
              'p',
              `기존 실제 경로: ${d.baseline.path} · Jev 결과는 비교 기록이며 경로 변경에 사용하지 않았습니다.`,
              root
            );
        }
      }
      el(
        'p',
        '독립 검토·현재 근거 확인은 기존 기준을 유지합니다. 모델 확신도는 정답률을 보증하지 않습니다.',
        root,
        'control-caption'
      );
      const calls = detail.calls.filter((c) => (c.queued_at || Infinity) <= at);
      if (calls.length) {
        const section = el('details', null, root);
        el('summary', `연결된 모델 호출 ${calls.length}개`, section);
        for (const c of calls) {
          const done = c.finished_at && c.finished_at <= at;
          el(
            'p',
            `${c.model} · ${names[c.role] || c.role} · ${done || cursor === null ? status(c.status) : '해당 시점 호출 진행'}${done ? ` · ${number(c.run_ms)} ms · 입력 ${number(c.input_tokens)} / 출력 ${number(c.output_tokens)} 토큰` : ''}`,
            section
          );
        }
      }
      const refs = el('details', null, root);
      el('summary', '저장 입력 근거 ' + detail.sources.length + '개', refs);
      detail.sources.forEach((s, i) => {
        const row = el('div', null, refs, 'control-source');
        el('span', String(i + 1).padStart(2, '0'), row);
        const box = el('div', null, row),
          url = safeURL(s.url);
        const title = el(url ? 'a' : 'span', s.title || s.id, box);
        if (url) {
          title.href = url;
          title.target = '_blank';
          title.rel = 'noopener noreferrer';
        }
        el('small', s.kind, box);
      });
    }
    root.scrollTop = scroll;
  }
  function renderDetail() {
    if (!detail) return;
    motion();
    const progress = $('control-progress');
    progress.replaceChildren();
    if (cursor !== null) el('span', '과거 단계 기록 재생 · 자동 따라가기 중지', progress);
    else {
      const p = detail.job.progress,
        running = p?.active || [];
      el(
        'strong',
        running.length
          ? running.map((s) => names[s.stage] || s.stage).join(' · ')
          : status(detail.job.status),
        progress
      );
      if (p?.total) {
        const bar = el('progress', null, progress);
        bar.max = 100;
        bar.value = p.percent;
        bar.setAttribute('aria-label', '현재 경로의 기록된 완료 단계');
        el('span', `${p.completed} / ${p.total} 단계 · ${p.percent}%`, progress);
      }
      el('small', '저장된 단계 완료 기준 · 시간 비율 아님', progress);
    }
    $('control-title').textContent = detail.job.title;
    $('control-result').hidden = false;
    $('control-result').href = detail.job.href;
    const events = detail.events || [],
      index = cursor === null ? events.length - 1 : events.findIndex((e) => e.id === cursor);
    $('control-time').max = Math.max(0, events.length - 1);
    $('control-time').value = Math.max(0, index);
    $('control-time').disabled = events.length < 2;
    $('control-play').disabled = events.length < 2;
    $('control-clock').textContent =
      (cursor === null ? '최신 기록 · ' : '기록 재생 · ') +
      (events[index] ? time(events[index].at) : '시각 없음');
    const list = $('control-events'),
      scroll = list.scrollTop,
      focused = document.activeElement?.dataset.event;
    list.replaceChildren();
    for (const [i, e] of events.entries()) {
      const li = el('li', null, list),
        b = el('button', null, li, 'control-event');
      b.dataset.event = e.id;
      b.dataset.future = String(i > index);
      if (i === index) b.setAttribute('aria-current', 'step');
      el('time', shortTime(e.at), b);
      el('span', (names[e.stage] || e.stage) + ' · ' + status(e.status), b);
      b.onclick = () => gotoIndex(i);
      if (focused === e.id) b.focus({ preventScroll: true });
    }
    if (!events.length) el('li', '저장된 단계 이력이 없습니다.', list);
    list.scrollTop = scroll;
    graph();
    lanes();
    signals();
    inspector();
  }
  window.WorkflowControl = {
    update,
    select,
    connection(value) {
      offline = value;
      motion();
    },
  };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stop();
      controller?.abort();
      detailVersion = '';
    }
  });
  addEventListener('pagehide', () => {
    closed = true;
    stop();
    controller?.abort();
  });
  addEventListener('pageshow', (e) => {
    if (e.persisted) {
      closed = false;
      detailVersion = '';
      if (latest) update(latest);
    }
  });
})();
