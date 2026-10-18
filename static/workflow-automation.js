/* The automatic pipeline shares the existing snapshot; it never submits analysis. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const labels = {
    running: '진행',
    preparing: '준비',
    refreshing: '갱신 중 · 질의 가능',
    available: '갱신 대기 · 질의 가능',
    queued: '대기',
    planned: '준비',
    complete: '완료 기록',
    verified: '검토 완료',
    recorded: '저장 기록',
    unknown: '기록 없음',
    live: '정기 확인 정상',
    delayed: '확인 지연',
    paused: '일시중지',
    failed: '실패',
    needs_review: '검토 필요',
    requires_review: '검토 필요',
    stale: '입력 변경',
    owner_missing: '실행자 없음',
    interrupted: '중단',
    waiting: '다음 처리 대기',
  };
  const titles = {
    collector: 'Telegram 확인',
    extract: '추출 · 중복 제거',
    baseline: '기본 분석 · 검토',
    deep: '상세 · 위험 검토',
    sources: '원문 보강',
    papers: '논문 분석',
    wiki: '위키 편찬',
    publication: '공개 게시 기록',
    index: '검토 자료 검색 반영',
    questions: '종합 질의 · 검토',
  };
  const roleNames = {
    baseline: '기본 분석',
    baseline_review: '기본 검토',
    baseline_analysis: '기사 기본 분석',
    baseline_verification: '기사 독립 검토',
    graph_draft: '종합 답변 작성',
    graph_answer: '질의 계획 · 보완',
    strategic_question_verification: '종합 답변 독립 검토',
    national: '국가 관점',
    technology: '기술 관점',
    risk_assessment: '위험 분석',
    verification: '독립 검토',
    integrated_analysis: '통합 분석',
    integrated_verification: '공동 검토',
    strategy_draft: '전략 작성',
    synthesis: '종합 작성',
    question_answer: '답변 작성',
  };
  const state = (v) => labels[v] || v || '기록 없음';
  const num = (v) => Number(v || 0).toLocaleString('ko-KR');
  const el = (tag, text, parent, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    parent?.append(e);
    return e;
  };
  const running = (s) =>
    Number(s.active_count) > 0 ||
    (['running', 'preparing'].includes(s.status) && s.owner_alive === true);
  const problems = (s) =>
    [
      'failed',
      'owner_missing',
      'needs_review',
      'requires_review',
      'paused',
      'delayed',
      'stale',
    ].includes(s.status);
  let latest = null,
    previous = null,
    stages = new Map(),
    selectedStage = '',
    stopped = true,
    received = 0,
    changes = [];
  function describe(s) {
    if (s.id === 'index' && s.status === 'complete') return '검토 자료 검색 준비 완료';
    if (s.id === 'collector')
      return s.status === 'live' ? '정기 확인 정상 · 새 메시지 대기' : state(s.status);
    if (running(s)) return '실행 확인 · ' + state(s.status === 'recorded' ? 'running' : s.status);
    return state(s.status);
  }
  function stageInfo() {
    const s = stages.get(selectedStage);
    const update = s?.updated_at
      ? ` 최근 반영 ${new Date(s.updated_at).toLocaleString('ko-KR')}.`
      : '';
    const delta =
      s?.mode === 'baseline_delta'
        ? ` 변경 ${num(s.changed)}개 · 철회 ${num(s.removed)}개만 검색에 반영했습니다.`
        : '';
    $('auto-stage-note').textContent = s
      ? `${s.title} · ${s.note}${s.error_message ? ' ' + s.error_message : ''}${update}${delta}`
      : '새 입력은 기존 작업의 체크포인트에서 우선 인계됩니다. 위험·논문은 병행 처리하며, 검토된 자료부터 검색과 질의에 반영합니다.';
    $('auto-stage-link').hidden = !s;
    if (s) $('auto-stage-link').href = s.href;
    document
      .querySelectorAll('.auto-stage')
      .forEach((b) =>
        b.setAttribute('aria-pressed', String(b.dataset.autoStage === selectedStage))
      );
  }
  function card(s, root, index, support) {
    let b = root.querySelector(`[data-auto-stage="${s.id}"]`);
    if (!b) {
      b = el('button', null, root, 'auto-stage');
      b.dataset.autoStage = s.id;
      b.onclick = () => {
        selectedStage = selectedStage === s.id ? '' : s.id;
        stageInfo();
      };
    }
    const before = previous?.stages.find((x) => x.id === s.id);
    const changed =
      before &&
      (before.updated_at !== s.updated_at ||
        before.complete !== s.complete ||
        before.status !== s.status);
    const signature = JSON.stringify(s);
    b.dataset.activity = running(s) ? 'running' : problems(s) ? 'attention' : 'idle';
    b.dataset.support = String(support);
    b.dataset.tick = String(!!changed);
    if (b.dataset.signature === signature) return;
    b.dataset.signature = signature;
    b.replaceChildren();
    const top = el('span', null, b, 'auto-stage-top');
    el('small', support ? 'PARALLEL' : '0' + (index + 1), top);
    el('i', null, top, 'auto-indicator');
    el('strong', titles[s.id] || s.title, b);
    el('span', describe(s), b, 'auto-stage-state');
    const total = s.total > 0;
    el(
      'b',
      total
        ? `${num(s.complete)} / ${num(s.total)}`
        : s.observed != null
          ? num(s.observed) + (s.id === 'index' ? '개 자료' : '건')
          : s.updated_at
            ? new Date(s.updated_at).toLocaleTimeString('ko-KR', { hour12: false })
            : '—',
      b,
      'auto-stage-value'
    );
    if (total) {
      const track = el('span', null, b, 'auto-track'),
        fill = el('i', null, track);
      fill.style.width = Math.max(0, Math.min(100, s.percent || 0)) + '%';
      el('small', `${s.percent ?? '—'}% · ${s.unit} 원장`, b);
      const counts = s.counts || {};
      const waiting = (counts.pending || 0) + (counts.queued || 0) + (counts.retry || 0);
      const attention = (counts.needs_review || 0) + (counts.failed || 0);
      el(
        'small',
        `대기 ${num(waiting)} · 실행 ${num(s.active_count ?? counts.running)} · 보류 ${num(attention)}`,
        b,
        'auto-stage-queue'
      );
    } else
      el(
        'small',
        s.id === 'publication'
          ? '배포 도구의 마지막 기록'
          : s.id === 'collector'
            ? '확인 시각 기준 · 완료율 없음'
            : s.unit,
        b
      );
    const counts = s.counts || {},
      review = (counts.needs_review || 0) + (counts.requires_review || 0) + (counts.stale || 0),
      failed = (counts.failed || 0) + (counts.blocked || 0);
    if (review || failed)
      el('small', `검토 ${num(review)} · 실패/차단 ${num(failed)}`, b, 'auto-attention');
  }
  function models(data) {
    const root = $('auto-models'),
      focus = document.activeElement?.dataset.call;
    const calls = (data.calls || []).filter(
      (c) => c.owner_alive && ['running', 'queued'].includes(c.status)
    );
    const active = data.call_counts?.running || 0,
      queued = data.call_counts?.queued || 0;
    $('auto-model-count').textContent =
      `실행 ${num(active)} / 한도 ${data.call_limit ?? '—'} · 대기 ${num(queued)} · 상세 최대 20건`;
    if (data.runtime_error)
      $('auto-model-count').textContent = '호출 원장 연결 확인 중 · 마지막 수신 기준';
    const ids = new Set(calls.map((c) => String(c.id)));
    for (const child of [...root.children]) if (!ids.has(child.dataset.call)) child.remove();
    if (!calls.length) {
      el(
        'span',
        data.runtime_error
          ? '호출 상태를 다시 확인합니다.'
          : '실행 중인 모델 호출 없음 · 다음 자동 작업을 기다립니다.',
        root,
        'auto-idle'
      );
      return;
    }
    for (const c of calls) {
      let box = root.querySelector(`[data-call="${c.id}"]`);
      if (!box) {
        box = el('div', null, root, 'auto-call');
        box.dataset.call = String(c.id);
      }
      box.dataset.status = c.status;
      const signature = JSON.stringify([c.model, c.role, c.status, c.started_at, c.queued_at]);
      if (box.dataset.signature !== signature) {
        box.dataset.signature = signature;
        box.replaceChildren();
        el('strong', (c.model || '모델').replace(/^gpt-/, ''), box);
        el('span', roleNames[c.role] || c.role || '분석', box);
        const age = el('small', null, box);
        age.dataset.elapsed = String(c.id);
      }
      box.querySelector('[data-elapsed]').textContent =
        `${state(c.status)} · ${num(c.elapsed_seconds)}초`;
      box.title = `호출 ${c.id} · ${c.model} · ${c.role} · ${state(c.status)}`;
      if (focus === String(c.id)) box.focus({ preventScroll: true });
    }
  }
  function record(data) {
    if (!previous) return;
    const before = new Map((previous.control?.jobs || []).map((j) => [j.key, j]));
    for (const s of data.stages || []) {
      const old = previous.stages?.find((p) => p.id === s.id);
      if (
        !old ||
        (old.status === s.status && old.complete === s.complete && old.updated_at === s.updated_at)
      )
        continue;
      changes.unshift({
        title: titles[s.id] || s.title,
        status: describe(s),
        at: data.observed_at,
        stage: s.id,
      });
    }
    for (const j of data.control?.jobs || []) {
      const old = before.get(j.key);
      if (old && old.status === j.status && old.progress?.revision === j.progress?.revision)
        continue;
      if (!old && !['running', 'preparing', 'queued'].includes(j.status)) continue;
      const phase = j.progress?.active?.map((s) => s.stage).join(', ');
      changes.unshift({
        key: j.key,
        title: j.title,
        status: state(j.status),
        at: data.observed_at,
        phase,
      });
    }
    changes = changes.slice(0, 10);
    const root = $('auto-changes');
    root.replaceChildren();
    el('small', '이 화면에서 확인된 변화', root);
    if (!changes.length) el('span', '새 상태 전환을 기다립니다.', root);
    for (const c of changes.slice(0, 4)) {
      const b = el(
        'button',
        `${new Date(c.at).toLocaleTimeString('ko-KR', { hour12: false })} · ${c.status} · ${c.title}`,
        root
      );
      b.title = c.phase || c.status;
      b.onclick = () => {
        if (c.stage) {
          selectedStage = c.stage;
          stageInfo();
        } else window.WorkflowControl?.select(c.key);
      };
    }
  }
  function tick() {
    if (!latest || stopped || document.hidden) return;
    const delta = Math.max(0, Math.floor((Date.now() - received) / 1000));
    for (const e of document.querySelectorAll('[data-elapsed]')) {
      const c = latest.calls.find((c) => String(c.id) === e.dataset.elapsed);
      if (!c) continue;
      e.textContent = `${state(c.status)} · ${num((c.elapsed_seconds || 0) + delta)}초`;
    }
  }
  window.WorkflowAutomation = {
    update(data) {
      latest = data;
      received = Date.now();
      stages = new Map(data.stages.map((s) => [s.id, s]));
      for (const [i, id] of ['collector', 'extract', 'baseline', 'index', 'questions'].entries())
        if (stages.has(id)) card(stages.get(id), $('auto-pipeline'), i, false);
      for (const [i, id] of ['deep', 'sources', 'papers', 'wiki', 'publication'].entries())
        if (stages.has(id)) card(stages.get(id), $('auto-support'), i, true);
      const activeJobs = (data.control?.jobs || []).filter((j) =>
        ['running', 'preparing'].includes(j.status)
      );
      const activeStages = data.stages.filter(running);
      $('auto-summary').textContent = activeJobs.length
        ? `진행 작업 ${num(activeJobs.length)}개 · 실제 단계 자동 갱신`
        : activeStages.length
          ? activeStages.map((s) => titles[s.id]).join(' · ') + ' 진행'
          : '현재 실행 작업 없음 · 다음 자동 처리 대기';
      if (data.control?.unavailable) $('auto-summary').textContent = '작업 원장 연결 확인 중';
      if (data.schedule?.enabled === 0) $('auto-summary').textContent += ' · 상세 분석 예약 꺼짐';
      stageInfo();
      models(data);
      record(data);
      previous = data;
      tick();
    },
    connection(value) {
      stopped = value;
      if (!value) tick();
    },
  };
  setInterval(tick, 1000);
  addEventListener('pagehide', () => {
    stopped = true;
  });
})();
