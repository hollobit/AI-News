(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const els = {
    runtime: $('#runtime'),
    runs: $('#runs'),
    refresh: $('#refresh'),
    content: $('#content'),
  };
  const state = {
    runtime: {},
    runs: [],
    run: null,
    seed: null,
    report: null,
    request: 0,
    seedRequest: 0,
    reportRequest: 0,
    timer: null,
    posting: false,
    runtimePosting: false,
  };
  const ACTIVE = new Set(['queued', 'preparing', 'starting', 'running', 'stop_requested']);
  const RESUMABLE = new Set(['paused', 'stopped', 'failed']);
  const STATUS = {
    draft: '자료 검토',
    pending: '대기',
    queued: '대기 중',
    preparing: '자료 구성 중',
    starting: '시작 중',
    running: '실행 중',
    stop_requested: '중지 요청됨',
    paused: '일시 중지',
    stopped: '중지됨',
    complete: '완료',
    completed: '완료',
    succeeded: '완료',
    failed: '실패',
    error: '오류',
    setup_required: '설정 필요',
    blocked: '실행 불가',
  };
  const STAGES = [
    ['graph', '그래프 구축', '기사와 플레이어의 관계를 지식 그래프로 구성'],
    ['agents', '에이전트 준비', '이해관계자 역할과 행동 조건을 준비'],
    ['simulation', '시뮬레이션', '격리된 SNS 환경에서 상호작용 실행'],
    ['report', '보고서', '관찰 결과와 조건부 시나리오 정리'],
  ];
  const text = (value) => (value == null ? '' : String(value));
  const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const clamp = (value) => Math.max(0, Math.min(1, number(value)));
  const node = (tag, className, value) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value != null) el.textContent = text(value);
    return el;
  };
  const dateTime = (value) => {
    if (!value) return '시각 미정';
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? text(value)
      : new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  };
  const safeLink = (value) => {
    try {
      const url = new URL(value, location.origin);
      return /^https?:$/.test(url.protocol) ? url.href : '';
    } catch {
      return '';
    }
  };
  const runId = (run) => text(run?.id || run?.run_id);
  const runStatus = (run) => text(run?.status || 'draft').toLowerCase();
  const runProgress = (run) => {
    const raw = run?.progress?.overall ?? run?.progress_percent ?? run?.progress ?? 0;
    return number(raw) > 1 ? clamp(number(raw) / 100) : clamp(raw);
  };
  const pill = (status) =>
    node(
      'span',
      `pill ${text(status).toLowerCase()}`,
      STATUS[text(status).toLowerCase()] || text(status) || '상태 미상'
    );
  const getRun = (data) => data?.run || data;

  async function request(url, options = {}) {
    return Workspace.request(url, {
      signal: options.signal,
      body: options.body === undefined ? undefined : JSON.parse(options.body),
    });
  }
  function stopPolling() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
  }
  function setUrl(id, replace = true) {
    const url = new URL(location.href);
    if (id) url.searchParams.set('run', id);
    else url.searchParams.delete('run');
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
  }
  function runtimeReady() {
    return state.runtime?.configured === true && state.runtime?.backend?.running === true;
  }
  function runtimeMissing() {
    const values = state.runtime?.missing_settings || [];
    return Array.isArray(values) ? values.map(text).filter(Boolean) : [];
  }
  function miroUrl() {
    return state.runtime?.frontend?.running === true
      ? safeLink(state.runtime.frontend.url || '')
      : '';
  }

  function renderRuntime() {
    const ready = runtimeReady(),
      missing = runtimeMissing();
    els.runtime.className = `runtime ${ready ? 'ready' : 'blocked'}`;
    const copy = node('div', 'runtime-copy');
    copy.append(
      node('strong', '', ready ? 'MiroFish 실행 환경 준비됨' : 'MiroFish 실행 설정 필요')
    );
    const installed = state.runtime?.installed === true ? '엔진 설치됨' : '엔진 설치 확인 필요',
      oasis = state.runtime?.oasis_available === true ? 'OASIS 사용 가능' : 'OASIS 확인 필요';
    const detail = ready
      ? `${installed} · ${oasis} · 그래프 생성과 시뮬레이션을 시작할 수 있습니다.`
      : missing.length
        ? `서버 환경에 필요한 설정: ${missing.join(', ')}. 이 화면에서는 비밀 값을 입력하거나 저장하지 않습니다.`
        : text(
            state.runtime?.reason ||
              `${installed} · MiroFish 백엔드를 실행한 뒤 시뮬레이션을 시작할 수 있습니다.`
          );
    copy.append(node('span', '', detail));
    els.runtime.replaceChildren(node('span', 'runtime-dot'), copy);
    const url = miroUrl();
    if (ready && url) {
      const link = node('a', 'miro-link', 'MiroFish 원본 UI');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.title = '에이전트 인터뷰, 동적 개입, 보고서 대화를 원본 MiroFish UI에서 엽니다.';
      els.runtime.append(link);
    } else if (
      state.runtime?.installed === true &&
      state.runtime?.configured === true &&
      state.runtime?.backend?.running !== true
    ) {
      const start = node(
        'button',
        'miro-link',
        state.runtimePosting ? '엔진 시작 중' : 'MiroFish 엔진 시작'
      );
      start.type = 'button';
      start.disabled = state.runtimePosting;
      start.addEventListener('click', startRuntime);
      els.runtime.append(start);
    }
  }

  function renderRuns() {
    els.runs.replaceChildren();
    if (!state.runs.length) {
      els.runs.append(
        node('li', 'rail-empty', '아직 저장된 시뮬레이션이 없습니다. 먼저 뉴스 자료를 구성하세요.')
      );
      return;
    }
    state.runs.forEach((run) => {
      const id = runId(run),
        li = node('li'),
        button = node('button', `run-button ${id === runId(state.run) ? 'active' : ''}`);
      button.type = 'button';
      const row = node('span', 'run-row');
      row.append(
        node('span', 'run-title', run.title || '제목 없는 시뮬레이션'),
        pill(runStatus(run))
      );
      button.append(row, node('span', 'run-id', `${dateTime(run.created_at)} · ${id}`));
      const track = node('span', 'run-progress'),
        fill = node('i');
      fill.style.width = `${Math.round(runProgress(run) * 100)}%`;
      track.append(fill);
      button.append(track);
      button.addEventListener('click', () => selectRun(id, false));
      li.append(button);
      els.runs.append(li);
    });
  }
  function loading(message) {
    els.content.replaceChildren(node('div', 'status-view', message));
  }
  function showError(message, retry) {
    const box = node('div', 'status-view error', message);
    if (retry) {
      const button = node('button', 'quiet-button', '다시 시도');
      button.type = 'button';
      button.addEventListener('click', retry);
      box.append(document.createElement('br'), button);
    }
    els.content.replaceChildren(box);
  }
  function section(title, description) {
    const root = node('section', 'section'),
      head = node('header', 'section-head'),
      copy = node('div');
    copy.append(node('h2', '', title));
    head.append(copy, node('p', '', description));
    root.append(head);
    return root;
  }

  function queryDefaults() {
    const params = new URLSearchParams(location.search);
    return {
      date: params.get('date') || '',
      topic: params.get('topic') || '',
      q: params.get('q') || '',
      keyword: params.get('keyword') || '',
      channel: params.get('channel') || '',
      content_type: params.get('content_type') || '',
    };
  }
  function inputField(label, name, value = '', options = null) {
    const wrap = node('div', 'field'),
      lab = node('label', '', label);
    lab.htmlFor = `sim-${name}`;
    let input;
    if (options) {
      input = node('select');
      options.forEach(([key, title]) => {
        const option = node('option', '', title);
        option.value = key;
        option.selected = key === value;
        input.append(option);
      });
    } else {
      input = node('input');
      input.type = name === 'rounds' ? 'number' : 'text';
      input.value = value;
    }
    input.id = `sim-${name}`;
    input.name = name;
    if (name === 'rounds') {
      input.min = '1';
      input.max = '40';
      input.value = value || '5';
    }
    wrap.append(lab, input);
    return wrap;
  }
  function renderDraftForm(root) {
    const shell = section(
        '자료 구성',
        '날짜와 주제를 좁혀 재현 가능한 뉴스 스냅샷을 만듭니다. 자료를 먼저 검토한 뒤 별도로 실행합니다.'
      ),
      form = node('form', 'form-card');
    form.id = 'simulation-form';
    const defaults = queryDefaults(),
      grid = node('div', 'field-grid');
    const title = inputField('시뮬레이션 제목', 'title', '');
    title.querySelector('input').required = true;
    title.querySelector('input').placeholder = '예: AI 규제 발표 이후 산업 반응';
    const date = inputField('날짜', 'date', defaults.date);
    date.querySelector('input').placeholder = 'YYYY-MM-DD 또는 all';
    const topic = inputField('주제 ID', 'topic', defaults.topic);
    topic.querySelector('input').placeholder = '전체 주제';
    const query = inputField('뉴스 검색어', 'q', defaults.q);
    query.querySelector('input').placeholder = '제목·본문 검색 · 최신 일치 뉴스 최대 20개';
    const keyword = inputField('키워드 ID', 'keyword', defaults.keyword);
    keyword.querySelector('input').placeholder = '선택 단어 ID';
    const channel = inputField('채널', 'channel', defaults.channel);
    channel.querySelector('input').placeholder = '전체 채널';
    const type = inputField('콘텐츠 유형', 'content_type', defaults.content_type, [
      ['', '전체'],
      ['news', '뉴스'],
      ['paper', '논문'],
      ['blog', '블로그'],
      ['other', '기타'],
    ]);
    const rounds = inputField('시뮬레이션 라운드', 'rounds', '5');
    const platform = inputField('시뮬레이션 플랫폼', 'platform', 'reddit', [
      ['reddit', 'Reddit (권장)'],
      ['twitter', 'Twitter'],
      ['parallel', 'Twitter + Reddit'],
    ]);
    const requirement = node('div', 'field wide');
    const requirementLabel = node('label', '', '시뮬레이션 요구사항');
    requirementLabel.htmlFor = 'sim-requirement';
    const textarea = node('textarea');
    textarea.id = 'sim-requirement';
    textarea.name = 'requirement';
    textarea.required = true;
    textarea.placeholder = '관찰할 이해관계자, 정책 변화, 질문과 조건을 구체적으로 적으세요.';
    requirement.append(
      requirementLabel,
      textarea,
      node(
        'p',
        'form-help',
        '결과는 입력한 자료와 조건에 따른 시뮬레이션 관찰입니다. 미래의 확정 사실로 취급하지 않습니다.'
      )
    );
    grid.append(title, date, topic, query, keyword, channel, type, rounds, platform, requirement);
    const actions = node('div', 'actions'),
      submit = node('button', 'primary', '자료 구성');
    submit.type = 'submit';
    submit.disabled = state.posting;
    actions.append(submit);
    form.append(grid, actions);
    form.addEventListener('submit', createDraft);
    shell.append(form);
    root.append(shell);
  }

  function normalizeSeed(seed, run) {
    const source = seed?.seed || seed?.snapshot || seed || run?.seed || run?.snapshot || {};
    const articles = source.articles || source.documents || source.items || run?.articles || [];
    return { ...source, articles: Array.isArray(articles) ? articles : [] };
  }
  function articleTitle(article) {
    return text(
      article.title || article.source_title || article.headline || article.url || '제목 없는 기사'
    );
  }
  function renderSnapshot(run, root) {
    const shell = section(
        '고정된 뉴스 자료',
        '이 목록은 실행 시점의 근거입니다. 이후 수집된 뉴스는 현재 시뮬레이션에 자동으로 섞이지 않습니다.'
      ),
      box = node('div', 'snapshot'),
      seed = normalizeSeed(state.seed, run),
      articles = seed.articles;
    const uniqueUrls = new Set(
      articles
        .map((article) => article.source_url || article.url || article.canonical_url)
        .filter(Boolean)
    ).size;
    const stats = node('div', 'snapshot-stats');
    [
      ['기사', seed.item_count ?? seed.article_count ?? seed.document_count ?? articles.length],
      ['고유 URL', seed.unique_url_count ?? seed.url_count ?? uniqueUrls],
      ['비공개 값 제거', seed.redaction_count ?? 0],
      ['라운드', run.rounds ?? run.config?.rounds ?? 5],
    ].forEach(([label, value]) => {
      const stat = node('div', 'stat');
      stat.append(
        node('span', '', label),
        node('strong', '', number(value).toLocaleString('ko-KR'))
      );
      stats.append(stat);
    });
    box.append(stats);
    if (!articles.length)
      box.append(
        node(
          'div',
          'empty',
          runStatus(run) === 'draft'
            ? '구성된 기사가 없습니다. 필터를 넓혀 새 자료를 구성하세요.'
            : '이 실행에 저장된 기사 목록이 없습니다.'
        )
      );
    else {
      box.append(
        node(
          'p',
          'snapshot-note',
          `자료 구성 시각 ${dateTime(seed.created_at || seed.frozen_at || run.created_at)} · 기사 제목과 출처 URL을 실행 근거로 고정했습니다.`
        )
      );
      const list = node('ol', 'article-list');
      articles.slice(0, 50).forEach((article) => {
        const li = node('li', 'article'),
          top = node('div', 'article-top'),
          title = node('h3'),
          href = safeLink(article.source_url || article.url || article.canonical_url);
        if (href) {
          const link = node('a', '', articleTitle(article));
          link.href = href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          title.append(link);
        } else title.textContent = articleTitle(article);
        top.append(title, node('span', 'article-date', article.date || article.day || ''));
        li.append(top);
        const meta = [
          article.source || article.domain,
          article.content_type_title || article.content_type,
          article.topic_title || article.topic,
        ]
          .filter(Boolean)
          .join(' · ');
        if (meta) li.append(node('p', '', meta));
        list.append(li);
      });
      box.append(list);
      if (articles.length > 50)
        box.append(
          node(
            'p',
            'more-row',
            `화면에는 50건까지 표시 · 전체 ${articles.length.toLocaleString('ko-KR')}건이 자료에 포함됨`
          )
        );
    }
    shell.append(box);
    root.append(shell);
  }

  function stageData(run, key, index) {
    const pipeline = run.pipeline || run.stages || run.progress?.stages || {};
    let value = Array.isArray(pipeline)
      ? pipeline.find((item) => item.key === key || item.id === key || item.name === key)
      : pipeline[key];
    if (value != null) {
      if (typeof value === 'string') value = { status: value };
      return {
        status: text(value.status || 'pending').toLowerCase(),
        progress:
          value.progress != null
            ? number(value.progress) > 1
              ? number(value.progress) / 100
              : number(value.progress)
            : 0,
        message: value.message || value.detail || '',
      };
    }
    const current = text(run.stage || 'draft').toLowerCase(),
      order = {
        draft: -1,
        ontology: 0,
        graph: 0,
        simulation_create: 1,
        simulation_prepare: 1,
        simulation_run: 2,
        report: 3,
        completed: 4,
      },
      currentIndex = order[current] ?? -1,
      status = runStatus(run);
    let stageStatus =
      index < currentIndex
        ? 'completed'
        : index === currentIndex
          ? status === 'failed'
            ? 'failed'
            : status === 'paused'
              ? 'paused'
              : status === 'stopped'
                ? 'stopped'
                : status === 'completed'
                  ? 'completed'
                  : 'running'
          : 'pending';
    if (currentIndex === 4) stageStatus = 'completed';
    return {
      status: stageStatus,
      progress:
        index < currentIndex || currentIndex === 4
          ? 1
          : index === currentIndex
            ? runProgress(run)
            : 0,
      message: '',
    };
  }
  function renderOverview(run, root) {
    const shell = section(
        '실행 흐름',
        '서버에서 보고한 실제 단계와 진행률입니다. 중지와 재개는 실행 상태를 보존해 요청합니다.'
      ),
      box = node('div', 'run-overview'),
      head = node('div', 'run-head'),
      info = node('div');
    info.append(
      pill(runStatus(run)),
      node('h2', '', run.title || '제목 없는 시뮬레이션'),
      node(
        'p',
        'run-meta',
        `실행 ID ${runId(run)} · 생성 ${dateTime(run.created_at)} · 시작 ${dateTime(run.started_at)} · 완료 ${dateTime(run.completed_at)}`
      )
    );
    head.append(info);
    box.append(head);
    const pipeline = node('div', 'pipeline');
    STAGES.forEach(([key, title, description], index) => {
      const data = stageData(run, key, index),
        card = node('article', 'stage'),
        top = node('div', 'stage-index');
      top.append(node('span', '', `0${index + 1}`), pill(data.status));
      card.append(top, node('h3', '', title), node('p', '', data.message || description));
      const bar = node('div', 'stage-bar'),
        fill = node('i');
      fill.style.width = `${Math.round(clamp(data.progress) * 100)}%`;
      bar.append(fill);
      card.append(bar);
      pipeline.append(card);
    });
    box.append(pipeline);
    const progress = node('div', 'progress-line'),
      track = node('div', 'progress-track'),
      fill = node('i'),
      overall = runProgress(run);
    fill.style.width = `${Math.round(overall * 100)}%`;
    track.append(fill);
    progress.append(track, node('strong', '', `${Math.round(overall * 100)}%`));
    box.append(progress);
    if (run.error || run.error_message)
      box.append(node('p', 'run-error', run.error || run.error_message));
    const actions = node('div', 'actions'),
      status = runStatus(run);
    if (status === 'draft') {
      const start = node('button', 'primary', '시뮬레이션 실행');
      start.type = 'button';
      start.disabled =
        state.posting || !runtimeReady() || !normalizeSeed(state.seed, run).articles.length;
      start.title = !runtimeReady() ? 'MiroFish 실행 환경 설정이 필요합니다.' : '';
      start.addEventListener('click', () => controlRun('start'));
      actions.append(start);
    }
    if (ACTIVE.has(status)) {
      const stop = node('button', 'danger', '실행 중지');
      stop.type = 'button';
      stop.disabled = state.posting;
      stop.addEventListener('click', () => controlRun('stop'));
      actions.append(stop);
    }
    if (RESUMABLE.has(status)) {
      const resume = node('button', 'secondary', '중단 지점에서 재개');
      resume.type = 'button';
      resume.disabled = state.posting || !runtimeReady();
      resume.addEventListener('click', () => controlRun('resume'));
      actions.append(resume);
    }
    if (actions.childElementCount) box.append(actions);
    shell.append(box);
    root.append(shell);
  }

  function reportText(run) {
    const response = state.report,
      report = response?.report || response || run.report || run.result?.report || run.result || '';
    if (typeof report === 'string') return report;
    if (report && typeof report === 'object') {
      const primary =
        report.markdown_content ||
        report.markdown ||
        report.content ||
        report.text ||
        report.summary;
      if (primary) return text(primary);
      if (report.title)
        return `${text(report.title)}\n\n${Array.isArray(report.sections) && report.sections.length ? JSON.stringify(report.sections, null, 2) : '상세 본문이 제공되지 않았습니다.'}`;
    }
    return '';
  }
  function renderReport(run, root) {
    const report = reportText(run);
    if (!report && !['complete', 'completed', 'succeeded'].includes(runStatus(run))) return;
    const shell = section(
        '시뮬레이션 보고서',
        '에이전트의 모의 상호작용에서 관찰한 패턴과 전략적 함의를 정리합니다.'
      ),
      card = node('div', 'report-card');
    card.append(
      node(
        'pre',
        'report-text',
        report || '서버에서 완료 상태를 반환했지만 표시할 보고서가 없습니다.'
      )
    );
    card.append(
      node(
        'p',
        'conditional',
        '이 보고서는 선택한 뉴스와 요구사항을 바탕으로 만든 조건부 시뮬레이션 결과입니다. 실제 여론, 실제 게시물 또는 미래의 확정 사실을 뜻하지 않습니다.'
      )
    );
    const url = miroUrl();
    if (runtimeReady() && url) {
      const tools = node('div', 'original-tools');
      tools.append(
        node(
          'p',
          '',
          '에이전트 인터뷰, 실행 중 동적 개입, 보고서 대화는 MiroFish 원본 UI에서 이어갈 수 있습니다.'
        )
      );
      const link = node('a', '', '원본 MiroFish UI에서 열기');
      link.href = url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      tools.append(link);
      card.append(tools);
    }
    shell.append(card);
    root.append(shell);
  }

  function render() {
    const root = node('div');
    renderDraftForm(root);
    if (state.run) {
      renderOverview(state.run, root);
      renderSnapshot(state.run, root);
      renderReport(state.run, root);
    }
    els.content.replaceChildren(root);
    renderRuns();
  }
  async function loadSeed(id, requestId) {
    const seedRequest = ++state.seedRequest;
    try {
      const data = await request(`/api/simulation/${encodeURIComponent(id)}/seed`);
      if (
        requestId !== state.request ||
        seedRequest !== state.seedRequest ||
        runId(state.run) !== id
      )
        return;
      state.seed = data;
      render();
    } catch (error) {
      if (requestId !== state.request || seedRequest !== state.seedRequest) return;
      state.seed = { articles: [], error: error.message };
      render();
    }
  }
  async function loadReport(id, requestId) {
    const reportRequest = ++state.reportRequest;
    try {
      const data = await request(`/api/simulation/${encodeURIComponent(id)}/report`);
      if (
        requestId !== state.request ||
        reportRequest !== state.reportRequest ||
        runId(state.run) !== id
      )
        return;
      state.report = data;
      render();
    } catch (error) {
      if (requestId !== state.request || reportRequest !== state.reportRequest) return;
      state.report = { report: { content: `보고서를 불러오지 못했습니다: ${error.message}` } };
      render();
    }
  }
  async function loadIndex(select = true) {
    stopPolling();
    const requestId = ++state.request;
    try {
      const data = await request('/api/simulation');
      if (requestId !== state.request) return;
      state.runtime = data.runtime || data.readiness || {};
      state.runs = Array.isArray(data.runs) ? data.runs : [];
      renderRuntime();
      renderRuns();
      if (!select) {
        render();
        return;
      }
      const wanted = new URLSearchParams(location.search).get('run'),
        selected = state.runs.find((item) => runId(item) === wanted) || state.runs[0];
      if (selected) await selectRun(runId(selected), true);
      else {
        state.run = null;
        state.seed = null;
        render();
      }
    } catch (error) {
      if (requestId !== state.request) return;
      state.runtime = { ready: false, message: error.message };
      state.runs = [];
      renderRuntime();
      Workspace.renderState(els.runs, 'error', '실행 목록 조회 실패', () => loadIndex());
      showError(error.message, () => loadIndex());
    }
  }
  async function selectRun(id, replace = false) {
    stopPolling();
    const requestId = ++state.request;
    setUrl(id, replace);
    loading('시뮬레이션 실행을 불러오는 중입니다.');
    try {
      const data = await request(`/api/simulation/${encodeURIComponent(id)}`);
      if (requestId !== state.request) return;
      state.run = getRun(data);
      state.seed = null;
      state.report = null;
      render();
      loadSeed(id, requestId);
      if (['complete', 'completed', 'succeeded'].includes(runStatus(state.run)))
        loadReport(id, requestId);
      if (ACTIVE.has(runStatus(state.run))) state.timer = setTimeout(() => refreshRun(id), 2500);
    } catch (error) {
      if (requestId !== state.request) return;
      showError(error.message, () => selectRun(id, true));
    }
  }
  async function refreshRun(id) {
    const requestId = ++state.request;
    try {
      const data = await request(`/api/simulation/${encodeURIComponent(id)}`);
      if (requestId !== state.request) return;
      state.run = getRun(data);
      const index = state.runs.findIndex((item) => runId(item) === id);
      if (index >= 0) state.runs[index] = { ...state.runs[index], ...state.run };
      render();
      if (ACTIVE.has(runStatus(state.run))) state.timer = setTimeout(() => refreshRun(id), 2500);
      else {
        await loadIndex(false);
        if (['complete', 'completed', 'succeeded'].includes(runStatus(state.run)))
          loadReport(id, state.request);
      }
    } catch (error) {
      if (requestId !== state.request) return;
      showError(
        `${error.message} 서버 작업은 계속될 수 있습니다. 새로고침해 상태를 확인하세요.`,
        () => selectRun(id, true)
      );
    }
  }
  async function createDraft(event) {
    event.preventDefault();
    if (state.posting) return;
    const form = event.currentTarget,
      data = new FormData(form),
      platform = text(data.get('platform')).trim();
    if (!['twitter', 'reddit', 'parallel'].includes(platform)) {
      showError('Reddit, Twitter 또는 병렬 플랫폼을 선택하세요.', render);
      return;
    }
    state.posting = true;
    render();
    const filters = {};
    ['date', 'topic', 'q', 'keyword', 'channel', 'content_type'].forEach((key) => {
      const value = text(data.get(key)).trim();
      if (value) filters[key] = value;
    });
    const payload = {
      filters,
      limit: 20,
      title: text(data.get('title')).trim(),
      requirement: text(data.get('requirement')).trim(),
      rounds: Math.max(1, Math.min(40, number(data.get('rounds')) || 5)),
      platform,
    };
    try {
      const response = await request('/api/simulation', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }),
        run = getRun(response),
        id = runId(run);
      if (!id) throw new Error('서버에서 실행 ID를 받지 못했습니다.');
      state.runs = [run, ...state.runs.filter((item) => runId(item) !== id)];
      await selectRun(id, false);
    } catch (error) {
      showError(error.message, render);
    } finally {
      state.posting = false;
      renderRuntime();
      if (state.run) render();
    }
  }
  async function controlRun(action) {
    const id = runId(state.run);
    if (!id || state.posting) return;
    state.posting = true;
    render();
    try {
      const data = await request(`/api/simulation/${encodeURIComponent(id)}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      state.run = { ...state.run, ...getRun(data) };
      render();
      if (ACTIVE.has(runStatus(state.run))) state.timer = setTimeout(() => refreshRun(id), 1200);
      else await loadIndex(false);
    } catch (error) {
      state.run = { ...state.run, error: error.message };
      render();
    } finally {
      state.posting = false;
      if (state.run) render();
    }
  }
  async function startRuntime() {
    if (state.runtimePosting) return;
    state.runtimePosting = true;
    renderRuntime();
    try {
      const data = await request('/api/simulation/runtime/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      state.runtime = data.runtime || data;
      renderRuntime();
      await loadIndex();
    } catch (error) {
      state.runtime = { ...state.runtime, reason: error.message };
      renderRuntime();
    } finally {
      state.runtimePosting = false;
      renderRuntime();
    }
  }

  els.refresh.addEventListener('click', () => loadIndex());
  window.addEventListener('popstate', () => loadIndex());
  loadIndex();
})();
