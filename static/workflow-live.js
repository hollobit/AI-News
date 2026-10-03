(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const labels = {
    live: '정상 확인',
    delayed: '확인 지연',
    complete: '완료',
    verified: '검증 완료',
    fetched: '확보',
    recorded: '원장 기록',
    running: '진행 중',
    queued: '호출 대기',
    pending: '대기',
    waiting: '다음 묶음 대기',
    planned: '준비',
    paused: '중지',
    failed: '실패',
    error: '오류',
    needs_review: '검토 필요',
    requires_review: '검토 필요',
    stale: '입력 변경',
    retry: '재시도',
    owner_missing: '실행자 없음',
    waiting_for_collector: 'Telegram 확인 대기',
    waiting_for_extraction: '기사 추출 완료 대기',
    recovery_backoff: '엔진 복구 대기',
    recovery_limit: '연속 실패로 복구 중지',
    engine_unavailable: '모델 서비스 복구 대기',
    started: '작업 시작',
    review_required: '검토 보완 필요',
    model_access_required: '모델 접근 확인 필요',
    disabled: '예약 처리 꺼짐',
    unknown: '기록 없음',
    interrupted: '중단',
    blocked: '차단',
  };
  const roles = {
    strategy_draft: '전략 초안 분석',
    integrated_reverification: '보완 통합 독립 검토',
    collection: '입력 자료 확인',
    enrichment: '외부 원문 보강',
    morphology: '핵심 표현 정리',
    graph_retrieval: '관련 근거 검색',
    national: '국가·정책 맥락 분석',
    review_plan: '검토 항목 설계',
    execution_plan: '분석 경로 선택',
    deliberation: '쟁점 검토',
    revision: '분석 보완',
    reverification: '보완 독립 검토',
    risk_revision: '위험 분석 보완',
    risk_reverification: '위험 보완 검토',
    final: '최종 결과 저장',
    source_repair: '원문 확보 재시도',
    repair_morphology: '보완 근거 정리',
    strategy_audit_reuse: '유효 검토 재사용',
    integrated_analysis: '통합 기사 분석',
    integrated_verification: '통합 독립 검토',
    technology: '기술 분석',
    verification: '전략 검토',
    risk_assessment: '위험 분석',
    risk_verification: '위험 독립 검토',
    synthesis: '분석 종합',
    wiki_compile: '위키 편찬',
    wiki_review: '위키 검토',
    wiki_structure: '위키 구조',
    baseline: '기본 분석',
    engine_probe: '엔진 복구 점검',
  };
  const duration = (s) =>
    s == null ? '기록 없음' : s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
  const fmt = (n) => Number(n || 0).toLocaleString('ko-KR');
  const date = (s) =>
    s ? new Date(typeof s === 'number' ? s * 1000 : s).toLocaleString('ko-KR') : '기록 없음';
  const label = (s) => labels[s] || s || '기록 없음';
  const tone = (s) =>
    ['complete', 'verified', 'fetched'].includes(s)
      ? 'ok'
      : ['running', 'queued'].includes(s)
        ? 'active'
        : ['needs_review', 'requires_review', 'stale', 'retry'].includes(s)
          ? 'review'
          : ['failed', 'error', 'blocked', 'interrupted', 'owner_missing'].includes(s)
            ? 'failed'
            : 'pending';
  const el = (tag, text, cls, parent) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    if (parent) parent.append(n);
    return n;
  };
  let current,
    selected = location.hash.slice(1) || 'deep',
    paused = false,
    focusAttention = false,
    inflight = false,
    timer,
    controller,
    closed = false,
    lastSuccess = 0;
  let samples = [],
    sampleRun,
    initialComplete;
  const cards = new Map();
  function needsAttention(s) {
    return (
      [
        'delayed',
        'paused',
        'failed',
        'error',
        'owner_missing',
        'needs_review',
        'requires_review',
      ].includes(s.status) ||
      (s.retries || []).length ||
      Object.entries(s.counts).some(([k, v]) => v && ['review', 'failed'].includes(tone(k)))
    );
  }
  function select(id) {
    selected = id;
    history.replaceState(null, '', '#' + id);
    if (current) render(current);
  }
  function card(s, index) {
    let c = cards.get(s.id);
    if (!c) {
      c = el('button', undefined, 'stage', $(index < 4 ? 'main-stages' : 'support-stages'));
      c.type = 'button';
      c.dataset.stage = s.id;
      c.onclick = () => select(s.id);
      const top = el('span', undefined, 'stage-top', c);
      el('span', String(index + 1).padStart(2, '0'), 'stage-number', top);
      el('span', '', 'stage-dot', top);
      el('span', s.title, 'stage-title', c);
      el('span', '', 'stage-value', c);
      el('span', '', 'stage-caption', c);
      el('span', undefined, 'progress-track', c);
      cards.set(s.id, c);
    }
    const signature = JSON.stringify([s.counts, s.status, s.observed]);
    if (c.dataset.signature && c.dataset.signature !== signature) {
      c.classList.remove('tick');
      void c.offsetWidth;
      c.classList.add('tick');
    }
    c.dataset.signature = signature;
    c.dataset.tone = needsAttention(s)
      ? ['failed', 'error', 'owner_missing', 'delayed'].includes(s.status)
        ? 'failed'
        : 'review'
      : s.status === 'live' || ['complete', 'verified'].includes(s.status)
        ? 'ok'
        : tone(s.status);
    c.setAttribute('aria-pressed', String(selected === s.id));
    c.classList.toggle('live', s.status === 'live' || (s.status === 'running' && s.owner_alive));
    c.classList.toggle('dim', focusAttention && !needsAttention(s));
    c.querySelector('.stage-dot').textContent = label(s.status);
    c.querySelector('.stage-value').textContent =
      s.percent !== null
        ? `${s.percent}%`
        : s.observed != null
          ? fmt(s.observed)
          : s.id === 'collector'
            ? s.status === 'live'
              ? '연결 정상'
              : '확인 필요'
            : s.publication
              ? '게시 기록 있음'
              : '—';
    const caption =
      s.percent !== null
        ? `${fmt(s.complete)} / ${fmt(s.total)} ${s.unit}`
        : s.observed != null
          ? s.unit
          : date(s.updated_at);
    c.querySelector('.stage-caption').textContent = caption;
    c.setAttribute(
      'aria-label',
      `${s.title}, ${label(s.status)}, ${caption}${s.percent !== null ? ', ' + s.percent + '%' : ''}`
    );
    const track = c.querySelector('.progress-track');
    track.replaceChildren();
    for (const [key, value] of Object.entries(s.counts)) {
      const seg = el('i', undefined, tone(key), track);
      seg.style.width = `${s.total ? (value / s.total) * 100 : 0}%`;
      seg.title = `${label(key)} ${fmt(value)}`;
    }
  }
  function detail(s) {
    const panel = $('detail');
    panel.replaceChildren();
    el('h2', s.title, '', panel).id = 'detail-title';
    const overview = el('div', undefined, 'detail-overview', panel);
    const donut = el('div', undefined, 'donut', overview);
    donut.style.setProperty('--angle', `${(s.percent || 0) * 3.6}deg`);
    el('span', s.percent === null ? '—' : s.percent + '%', '', donut);
    const info = el('div', undefined, '', overview);
    el('strong', label(s.status), '', info);
    el(
      'p',
      s.total
        ? `${fmt(s.complete)} / ${fmt(s.total)} ${s.unit} · 남은 대상 ${fmt(s.total - s.complete)}`
        : s.observed != null
          ? `${fmt(s.observed)} ${s.unit}`
          : '완료율을 계산하지 않는 단계입니다.',
      '',
      info
    );
    el('p', s.note, 'detail-note', panel);
    if (s.error_message) el('p', s.error_message, 'retry-note', panel);
    const grid = el('div', undefined, 'count-grid', panel);
    for (const [key, value] of Object.entries(s.counts)) {
      const box = el('div', undefined, '', grid);
      el('span', label(key), '', box);
      el('strong', fmt(value), '', box);
    }
    if (s.owner_alive !== undefined)
      el(
        'p',
        `분석 실행자: ${s.owner_alive ? '생존 확인' : '현재 실행자 없음'} · 원장의 진행 건수에는 체크포인트·대기가 포함될 수 있습니다.`,
        'detail-note',
        panel
      );
    if (s.updated_at) el('p', `마지막 갱신 ${date(s.updated_at)}`, 'detail-note', panel);
    if (s.run_id) el('p', `현재 실행 ${s.run_id.slice(0, 12)}`, 'detail-note', panel);
    for (const r of s.retries || [])
      el(
        'p',
        `회차 ${r.number} · 일시 오류 ${r.failures}회 · 재시도 가능 ${date(r.next_attempt_at)}`,
        'retry-note',
        panel
      );
    if (s.id === 'deep') {
      const z = current.schedule;
      if (z.stage)
        el('p', `예약 처리: ${label(z.stage)} · 확인 ${date(z.checked_at)}`, 'detail-note', panel);
      el(
        'p',
        `복구 이력 ${fmt(z.recoveries)}회 · 진척 없는 복구 ${fmt(z.stagnant)} / 3회`,
        'detail-note',
        panel
      );
    }
    if (s.publication) el('p', `게시 커밋 ${s.publication.commit}`, 'detail-note', panel);
    const link = el('a', '상세 운영 화면으로 이동 ↗', 'detail-link', panel);
    link.href = s.href;
  }
  function render(data) {
    current = data;
    if (!data.stages.some((s) => s.id === selected)) selected = 'deep';
    data.stages.forEach(card);
    detail(data.stages.find((s) => s.id === selected));
    $('scope').textContent = data.scope;
    const deep = data.stages.find((s) => s.id === 'deep');
    $('done').textContent = deep.total ? fmt(deep.complete) : '—';
    $('denominator').textContent =
      `${fmt(deep.total)}건 중 ${deep.percent === null ? '진척률 미산정' : deep.percent + '%'}`;
    $('remaining').textContent = deep.total ? fmt(deep.total - deep.complete) : '—';
    const active =
      data.call_counts?.running ??
      data.calls.filter((c) => c.status === 'running' && c.owner_alive).length;
    $('workers').textContent = data.runtime_error
      ? '조회 지연'
      : `${active} / ${data.call_limit ?? '—'}`;
    $('worker-note').textContent =
      `공유 모델 호출 상한 · 대기 ${data.call_counts?.queued ?? data.calls.filter((c) => c.status === 'queued').length}건`;
    if (sampleRun !== deep.run_id) {
      samples = [];
      sampleRun = deep.run_id;
      initialComplete = deep.complete;
    }
    if (!samples.length || samples.at(-1).at !== data.observed_at)
      samples.push({ value: deep.complete, at: data.observed_at });
    if (samples.length > 120) samples.shift();
    const change = deep.complete - initialComplete;
    $('delta').textContent = (change > 0 ? '+' : '') + fmt(change) + '건';
    const values = samples.map((s) => s.value),
      min = Math.min(...values),
      range = Math.max(1, Math.max(...values) - min);
    $('trend')
      .querySelector('polyline')
      .setAttribute(
        'points',
        samples
          .map(
            (s, i) =>
              `${(i / Math.max(1, samples.length - 1)) * 240},${33 - ((s.value - min) / range) * 29}`
          )
          .join(' ')
      );
    $('trend').setAttribute(
      'aria-label',
      `최근 화면 관측 구간 완료 변화 ${change}건. 입력 재대조로 감소할 수 있습니다.`
    );
    $('chart-value').textContent = deep.total ? fmt(deep.complete) + '건' : '—';
    $('chart-change').textContent =
      `${change > 0 ? '▲ +' : change < 0 ? '▼ ' : '— '}${fmt(change)}건 · 화면 진입 이후`;
    $('chart-change').className = change < 0 ? 'quote-down' : 'quote-up';
    const chartStart = Date.parse(samples[0].at);
    const chartSpan = Math.max(1, Date.parse(samples.at(-1).at) - chartStart);
    const chartPoints = samples
      .map(
        (s) =>
          `${((Date.parse(s.at) - chartStart) / chartSpan) * 600},${155 - ((s.value - min) / range) * 130}`
      )
      .join(' ');
    $('chart-line').setAttribute('points', chartPoints);
    $('chart-area').setAttribute(
      'points',
      samples.length > 1 ? `0,180 ${chartPoints} 600,180` : ''
    );
    $('chart-empty').hidden = samples.length > 1;
    const shortTime = (at) => new Date(at).toLocaleTimeString('ko-KR', { hour12: false });
    $('chart-start').textContent = shortTime(samples[0].at);
    $('chart-end').textContent = shortTime(samples.at(-1).at);
    $('chart-range').textContent = `${fmt(min)}–${fmt(Math.max(...values))}건`;
    $('market-chart').setAttribute(
      'aria-label',
      `상세 분석 완료 ${fmt(min)}에서 ${fmt(Math.max(...values))}건 범위, 최근 ${samples.length}개 실측값`
    );
    const query = $('task-search').value.trim().toLocaleLowerCase();
    const statusFilter = $('task-status').value;
    const matches = (t) =>
      (!query || `${t.number} ${t.title || ''}`.toLocaleLowerCase().includes(query)) &&
      (statusFilter === 'all' ||
        (statusFilter === 'complete' && t.status === 'complete') ||
        (statusFilter === 'active' &&
          ['running', 'planned', 'pending', 'queued'].includes(t.status)) ||
        (statusFilter === 'attention' &&
          (t.retry ||
            ['failed', 'needs_review', 'paused', 'interrupted'].includes(t.status) ||
            (t.steps || []).some((step) =>
              ['failed', 'retry', 'owner_missing', 'interrupted'].includes(step.status)
            ))));
    const visibleTasks = (data.tasks || []).filter(matches);
    $('task-count').textContent = `${visibleTasks.length} / ${(data.tasks || []).length}건`;
    const taskScroll = $('tasks').scrollTop;
    const focusedTask = document.activeElement?.closest('#tasks details')?.dataset.task;
    const expanded = new Set(
      [...document.querySelectorAll('#tasks details[open]')].map((n) => n.dataset.task)
    );
    $('tasks').dataset.loaded = 'true';
    $('tasks').replaceChildren();
    for (const task of visibleTasks) {
      const item = el('details', undefined, 'task', $('tasks'));
      item.dataset.task = String(task.id || task.number);
      item.open = expanded.has(item.dataset.task);
      const summary = el('summary', undefined, '', item);
      if (focusedTask === item.dataset.task) summary.focus({ preventScroll: true });
      el('small', `#${task.number} · ${label(task.status)}`, tone(task.status), summary);
      el('strong', task.title || '기사 체크포인트', '', summary).title =
        task.title || '기사 체크포인트';
      const steps = task.steps || [];
      const active = steps.filter((s) => ['running', 'retry', 'owner_missing'].includes(s.status));
      const latest = task.latest;
      el(
        'p',
        active.length
          ? active.map((s) => `${roles[s.stage] || s.stage} · ${label(s.status)}`).join(' / ')
          : latest
            ? `${roles[latest.stage] || label(latest.stage)} · ${label(latest.status)}`
            : label(task.status),
        '',
        summary
      );
      if (steps.length) {
        el(
          'p',
          task.step_percent == null
            ? '분석 경로 결정 중 · 완료율 미정'
            : `단계 완료 ${task.step_complete}/${task.step_total} · ${task.step_percent}%`,
          'task-progress-label',
          summary
        );
        const progress = el('progress', undefined, 'task-progress', summary);
        progress.max = task.step_total;
        if (task.step_percent != null) progress.value = task.step_complete;
        progress.setAttribute('aria-label', `회차 ${task.number} 단계 완료`);
        el(
          'p',
          `회차 경과 ${duration(task.elapsed_seconds)} · ${task.route || '경로 미정'}`,
          '',
          item
        );
        if (task.retrieval_timing?.total_ms != null) {
          const r = task.retrieval_timing;
          el(
            'p',
            `관련 근거 검색 ${r.total_ms}ms · 후보 조회 ${r.source_lookup_ms}ms · 현재 검토 통과 분석 ${r.validated_workflows}건`,
            '',
            item
          );
        }
        if (task.metrics) {
          const m = task.metrics;
          el(
            'p',
            `연결된 호출 ${m.recorded_calls}회 · 큐 대기 합계 ${m.queue_seconds}초 · 모델 실행 합계 ${m.model_seconds}초 · 입력 ${fmt(m.input_chars)}자 · 재사용 단계 ${task.reused_stages || 0}개`,
            '',
            item
          );
          const u = m.usage;
          if (u?.measured_calls) {
            el(
              'p',
              `토큰 실측 ${u.measured_calls}/${m.recorded_calls}회 · 입력 ${fmt(u.input_tokens)} · 입력 중 캐시 ${u.cached_input_tokens == null ? '미제공' : fmt(u.cached_input_tokens)} · 출력 ${fmt(u.output_tokens)} · 출력 중 추론 ${u.reasoning_output_tokens == null ? '미제공' : fmt(u.reasoning_output_tokens)}`,
              '',
              item
            );
          }
          el(
            'p',
            '현재 작업에 연결된 저장 호출 최대 100개 기준입니다. 실패·진행 중 호출은 누락될 수 있으며 병렬 시간 합계는 회차 경과 시간과 다릅니다. 입력 글자 수는 토큰 수가 아닙니다.',
            'task-note',
            item
          );
        }
        if (task.retry)
          el(
            'p',
            `실행 오류 ${task.retry.failures}회 · 재시도 가능 ${date(task.retry.next_attempt_at)} (자동 실행 보장 시각 아님)`,
            'task-warning',
            item
          );
        const list = el('ol', undefined, 'task-steps', item);
        for (const step of steps) {
          const li = el('li', undefined, tone(step.status), list);
          el('span', roles[step.stage] || step.stage, '', li);
          el(
            'span',
            `${label(step.status)}${step.elapsed_seconds == null ? '' : ` · ${duration(step.elapsed_seconds)}`}`,
            '',
            li
          );
          if (step.updated_at)
            el(
              'small',
              `갱신 ${date(step.updated_at)}${step.attempts > 1 ? ` · 시작 기록 ${step.attempts}회` : ''}`,
              '',
              li
            );
        }
        el(
          'p',
          '단계 수 기준이며 예상 소요 시간 비율이 아닙니다. 병렬 실행·재사용·추가 보완에 따라 달라집니다. 단계 완료와 검토 통과는 별개입니다.',
          'task-note',
          item
        );
      }
    }
    if (!visibleTasks.length)
      el(
        'p',
        (data.tasks || []).length
          ? '검색 조건에 맞는 작업이 없습니다.'
          : '현재 발행된 상세 분석 작업이 없습니다.',
        'empty',
        $('tasks')
      );
    $('tasks').scrollTop = taskScroll;
    $('calls').replaceChildren();
    if (data.runtime_error)
      el(
        'p',
        '모델 작업 상태를 읽지 못했습니다. 다음 갱신에서 다시 확인합니다.',
        'empty',
        $('calls')
      );
    else if (!data.calls.length)
      el('p', '현재 실행 중이거나 대기 중인 모델 호출이 없습니다.', 'empty', $('calls'));
    for (const call of data.calls) {
      const item = el('div', undefined, 'call', $('calls'));
      const text = el('div', undefined, '', item);
      el('strong', roles[call.role] || call.role, '', text);
      el('small', `${call.model} · ${label(call.status)}`, '', text);
      el('span', `${fmt(call.elapsed_seconds)}초`, '', item);
    }
    $('events').replaceChildren();
    for (const e of data.events) {
      const li = el('li', undefined, '', $('events'));
      const a = el('div', undefined, '', li);
      el('span', `회차 ${e.number} · ${label(e.status)}`, e.status, a);
      el('small', ' · ' + date(e.completed_at || e.created_at), '', a);
      el('span', e.status === 'complete' ? '✓' : e.status === 'running' ? '↻' : '•', e.status, li);
    }
    if (!data.events.length)
      el('li', '아직 기록된 상세 분석 회차가 없습니다.', 'empty', $('events'));
  }
  function connection() {
    const stale = !lastSuccess || Date.now() - lastSuccess > 12000;
    $('main').dataset.offline = String(stale || paused);
    $('connection').textContent = paused
      ? '화면 갱신 일시정지'
      : document.hidden
        ? '백그라운드 · 갱신 대기'
        : stale
          ? '연결 확인 중'
          : 'LIVE · 실시간 연결';
  }
  async function refresh() {
    if (inflight || closed) return;
    clearTimeout(timer);
    inflight = true;
    controller = new AbortController();
    try {
      const data = await Workspace.request('/api/operations/flow', {
        timeout: 10000,
        signal: controller.signal,
      });
      if (closed) return;
      render(data);
      lastSuccess = Date.now();
      $('updated').textContent = '마지막 수신 ' + date(data.observed_at);
      $('notice').hidden = true;
    } catch (error) {
      if (!closed) {
        $('notice').hidden = false;
        $('notice').textContent =
          '상태 연결이 지연되고 있습니다. 마지막 자료를 유지하며 다시 연결합니다.';
        $('connection').textContent = '연결 지연';
        lastSuccess = 0;
      }
    } finally {
      inflight = false;
      connection();
      if (!closed && !paused && !document.hidden) timer = setTimeout(refresh, 3000);
    }
  }
  $('pause').onclick = () => {
    paused = !paused;
    $('pause').setAttribute('aria-pressed', String(paused));
    $('pause').textContent = paused ? '화면 갱신 재개' : '화면 갱신 멈춤';
    clearTimeout(timer);
    connection();
    if (!paused) refresh();
  };
  $('refresh').onclick = refresh;
  $('task-search').oninput = () => {
    if (current) render(current);
  };
  $('task-status').onchange = () => {
    if (current) render(current);
  };
  function filter(value) {
    focusAttention = value;
    $('filter-all').setAttribute('aria-pressed', String(!value));
    $('filter-attention').setAttribute('aria-pressed', String(value));
    if (current) render(current);
  }
  $('filter-all').onclick = () => filter(false);
  $('filter-attention').onclick = () => filter(true);
  document.addEventListener('visibilitychange', () => {
    clearTimeout(timer);
    connection();
    if (!document.hidden && !paused) refresh();
  });
  addEventListener('pagehide', () => {
    closed = true;
    clearTimeout(timer);
    controller?.abort();
  });
  addEventListener('pageshow', (event) => {
    if (event.persisted) {
      closed = false;
      if (!paused) refresh();
    }
  });
  addEventListener('hashchange', () => {
    selected = location.hash.slice(1);
    if (current) render(current);
  });
  refresh();
})();
