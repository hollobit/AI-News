(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const els = {
    start: $('#start'),
    refresh: $('#refresh'),
    runs: $('#runs'),
    content: $('#content'),
  };
  const state = {
    enabled: false,
    runs: [],
    run: null,
    request: 0,
    docsRequest: 0,
    controller: null,
    timer: null,
    docsPage: 1,
    playerType: 'all',
    targetDocument: '',
    posting: false,
  };
  const ACTIVE = new Set(['queued', 'running']);
  const STATUS = {
    queued: '대기 중',
    running: '분석 중',
    paused: '중단됨',
    complete: '완료',
    complete_with_failures: '일부 실패로 완료',
    failed: '실패',
  };
  const PLAYER_TYPES = {
    all: '전체',
    country: '국가',
    company: '기업',
    person: '전문가',
    institution: '기관',
  };
  const SOURCE_STATUS = {
    pending: '원문 대기',
    full: '원문 전체',
    excerpt: '일부 발췌',
    failed: '원문 실패',
    not_applicable: '메시지 본문',
  };
  const DOC_STATUS = {
    pending: '분석 대기',
    running: '분석 중',
    complete: '요약 완료',
    failed: '요약 실패',
  };
  const text = (value) => (value == null ? '' : String(value));
  const node = (tag, className, value) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value != null) el.textContent = text(value);
    return el;
  };
  const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const percent = (value) => `${Math.round(Math.max(0, Math.min(1, number(value))) * 100)}%`;
  const dateTime = (value) => {
    if (!value) return '시각 미정';
    const d = new Date(value);
    return Number.isNaN(d.getTime())
      ? text(value)
      : new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeStyle: 'short' }).format(d);
  };
  const safeLink = (url) => {
    try {
      const parsed = new URL(url, location.origin);
      return /^https?:$/.test(parsed.protocol) ? parsed.href : '';
    } catch {
      return '';
    }
  };
  const statusPill = (status, coverage) => {
    const incomplete = status === 'complete' && coverage && coverage.complete !== true;
    return node(
      'span',
      `status-pill ${incomplete ? 'complete_with_failures' : text(status)}`,
      incomplete ? '범위 미완료' : STATUS[status] || text(status) || '상태 미상'
    );
  };

  function stopPolling() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    if (state.controller) state.controller.abort();
    state.controller = null;
  }
  function apiError(response, data, fallback) {
    const message = data && typeof data.error === 'string' ? data.error : fallback;
    const error = new Error(message);
    error.status = response.status;
    return error;
  }
  async function request(url, options = {}) {
    return Workspace.request(url, {
      signal: options.signal,
      body: options.body === undefined ? undefined : JSON.parse(options.body),
    });
  }
  function updateUrl(runId, documentId = '', replace = true) {
    const url = new URL(location.href);
    if (runId) url.searchParams.set('run', runId);
    else url.searchParams.delete('run');
    if (documentId) url.searchParams.set('document', documentId);
    else url.searchParams.delete('document');
    url.hash = documentId ? 'documents' : '';
    history[replace ? 'replaceState' : 'pushState']({}, '', url);
  }
  function currentActive() {
    return state.runs.some((run) => ACTIVE.has(run.status));
  }
  function updateStart() {
    els.start.disabled = !state.enabled || currentActive() || state.posting;
    els.start.textContent = state.posting
      ? '분석 스냅샷 만드는 중'
      : currentActive()
        ? '종합 분석 진행 중'
        : '수집 완료 · 종합 분석';
    els.start.title = !state.enabled
      ? '종합 분석 서비스가 비활성화되어 있습니다.'
      : currentActive()
        ? '현재 분석이 끝난 뒤 새 스냅샷을 만들 수 있습니다.'
        : '';
  }

  function renderRuns() {
    els.runs.replaceChildren();
    if (!state.runs.length) {
      els.runs.append(
        node(
          'li',
          'rail-empty',
          '아직 만든 분석 스냅샷이 없습니다. 수집이 끝난 시점에 종합 분석을 시작하세요.'
        )
      );
      return;
    }
    state.runs.forEach((run) => {
      const li = node('li');
      const button = node('button', `run-button ${state.run?.id === run.id ? 'active' : ''}`);
      button.type = 'button';
      const row = node('span', 'run-row');
      row.append(
        node('span', 'run-date', dateTime(run.created_at)),
        statusPill(run.status, run.coverage)
      );
      const id = node('span', 'run-id', `범위 ID ${run.id}`);
      const track = node('span', 'run-progress');
      const fill = node('i');
      fill.style.width = percent(run.progress);
      track.append(fill);
      button.append(row, id, track);
      button.addEventListener('click', () => selectRun(run.id));
      li.append(button);
      els.runs.append(li);
    });
  }

  function loading(message) {
    els.content.replaceChildren();
    const box = node('div', 'status');
    box.append(node('div', 'spinner'), document.createTextNode(message));
    els.content.append(box);
  }
  function errorView(message, retry) {
    els.content.replaceChildren();
    const box = node('div', 'status error', message);
    if (retry) {
      const button = node('button', 'refresh', '다시 불러오기');
      button.type = 'button';
      button.addEventListener('click', retry);
      box.append(document.createElement('br'), button);
    }
    els.content.append(box);
  }
  function emptyView() {
    els.content.replaceChildren(
      node(
        'div',
        'empty-block',
        '아직 종합 분석 결과가 없습니다. 메시지와 URL 수집이 끝나면 위 버튼으로 현재 전체 자료의 스냅샷을 만드세요.'
      )
    );
  }

  function evidenceLinks(ids, runId) {
    const wrap = node('div', 'evidence-links');
    [...new Set(Array.isArray(ids) ? ids.map(text) : [])].forEach((id) => {
      const link = node('a', 'evidence-link', `근거 ${id}`);
      link.href = `/research?run=${encodeURIComponent(runId)}&document=${encodeURIComponent(id)}#documents`;
      link.addEventListener('click', (event) => {
        event.preventDefault();
        openEvidence(id);
      });
      wrap.append(link);
    });
    return wrap;
  }
  function barRows(rows, labelKey = 'topic', extraClass = '') {
    const list = node('ol', `bar-list ${extraClass}`);
    (rows || []).forEach((row) => {
      const li = node('li');
      const label = node('div', 'bar-label');
      label.append(
        node('strong', '', row[labelKey] || '미분류'),
        node('span', '', `${number(row.count).toLocaleString('ko-KR')}건 · ${percent(row.share)}`)
      );
      const bar = node('div', 'bar');
      const fill = node('i');
      fill.style.width = percent(row.share);
      bar.append(fill);
      li.append(label, bar);
      list.append(li);
    });
    return list;
  }

  function renderProgress(run, root) {
    const coverage = run.coverage || {};
    const card = node('section', 'progress-card');
    const top = node('div', 'progress-top');
    top.append(
      node('strong', '', percent(run.progress)),
      node(
        'span',
        '',
        `${number(run.processed_documents).toLocaleString('ko-KR')} / ${number(run.total_documents).toLocaleString('ko-KR')} 문서 처리`
      )
    );
    const track = node('div', 'progress-track');
    const fill = node('i');
    fill.style.width = percent(run.progress);
    track.append(fill);
    const grid = node('div', 'coverage-grid');
    [
      ['분석 성공', coverage.successful_documents ?? run.successful_documents, ''],
      ['원문 전체 확보', coverage.source_full, ''],
      ['발췌·메시지 기반', coverage.source_excerpt, ''],
      ['수집·분석 실패', coverage.source_failed ?? run.failed_documents, 'failed'],
    ].forEach(([label, value, cls]) => {
      const cell = node('div', `coverage-cell ${cls}`);
      cell.append(
        node('span', '', label),
        node('strong', '', number(value).toLocaleString('ko-KR'))
      );
      grid.append(cell);
    });
    const coverageValue = coverage.analysis_coverage ?? run.progress;
    const complete = coverage.complete === true;
    const note = node(
      'p',
      `coverage-note ${complete ? 'ok' : ''}`,
      complete
        ? `분석 범위의 ${percent(coverageValue)}를 처리했습니다. 실패 문서는 아래 원문 목록에서 확인할 수 있습니다.`
        : `현재 결과는 전체 범위의 ${percent(coverageValue)}를 바탕으로 한 중간 결과입니다. 완료되기 전에는 전체 흐름으로 단정하지 마세요.`
    );
    card.append(top, track, grid, note);
    root.append(card);
  }

  function renderMetrics(run, root) {
    const metrics = run.metrics || {};
    if (
      !(metrics.topic_weights || []).length &&
      !(metrics.by_day || []).length &&
      !(metrics.player_mentions || []).length
    )
      return;
    const section = sectionShell(
      '채널 내 관심 비중',
      '주제·국가·참여자 비중은 분석에 성공한 문서 기준입니다. 실패한 문서를 주제와 무관한 것으로 계산하지 않으며, 대중 여론이나 시장 점유율을 뜻하지 않습니다. 한 문서가 여러 주제·국가에 포함될 수 있어 비중의 합이 100%를 넘을 수 있습니다.'
    );
    section.append(
      node(
        'p',
        'section-note',
        `전체 ${number(metrics.document_count)}개 중 ${number(metrics.analyzed_document_count)}개 분석 · 분석 범위 ${percent(metrics.analysis_coverage)}`
      )
    );
    const grid = node('div', 'metric-grid');
    const topics = node('div', 'panel');
    topics.append(node('h3', '', '주제별 보도 비중'), barRows(metrics.topic_weights || []));
    const days = node('div', 'panel');
    days.append(node('h3', '', '날짜별 보도량'), barRows(metrics.by_day || [], 'day', 'day-bars'));
    grid.append(topics, days);
    if ((metrics.daily_topic_weights || []).length) {
      const dailyTopics = node('div', 'panel daily-topics');
      const rows = metrics.daily_topic_weights.map((item) => ({
        ...item,
        label: `${item.day || '날짜 미상'} · ${item.topic || '미분류'}`,
      }));
      dailyTopics.append(node('h3', '', '날짜별 주제 흐름'), barRows(rows, 'label'));
      grid.append(dailyTopics);
    }
    section.append(grid);
    const playerPanel = node('div', 'panel');
    playerPanel.style.marginTop = '22px';
    playerPanel.append(node('h3', '', '주요 플레이어 언급 비중'));
    const tabs = node('div', 'player-tabs');
    Object.entries(PLAYER_TYPES).forEach(([key, label]) => {
      const button = node('button', state.playerType === key ? 'active' : '', label);
      button.type = 'button';
      button.addEventListener('click', () => {
        state.playerType = key;
        renderRun();
      });
      tabs.append(button);
    });
    const players = [...(metrics.player_mentions || [])];
    (metrics.country_mentions || []).forEach((item) => {
      if (
        !players.some(
          (row) => text(row.name) === text(item.name) && text(row.type).toLowerCase() === 'country'
        )
      )
        players.push({ ...item, type: 'country' });
    });
    const reportPlayers = new Map(
      (run.report?.players || []).map((item) => [
        `${text(item.type).toLowerCase()}\u0000${text(item.name)}`,
        item,
      ])
    );
    const list = node('ol', 'players');
    players
      .filter(
        (item) => state.playerType === 'all' || text(item.type).toLowerCase() === state.playerType
      )
      .forEach((item) => {
        const li = node('li', 'player');
        const type = text(item.type).toLowerCase();
        const detail = reportPlayers.get(`${type}\u0000${text(item.name)}`);
        li.dataset.type = type;
        li.append(
          node('strong', '', item.name || '이름 미상'),
          node(
            'span',
            '',
            `${PLAYER_TYPES[type] || item.type || '기타'} · ${number(item.count).toLocaleString('ko-KR')}회 · ${percent(item.share)}`
          )
        );
        if (detail?.role) li.append(node('span', '', detail.role));
        if (detail?.evidence_doc_ids?.length)
          li.append(evidenceLinks(detail.evidence_doc_ids, run.id));
        list.append(li);
      });
    if (!list.childElementCount)
      list.append(node('li', 'empty-block', '이 유형으로 분류된 플레이어가 없습니다.'));
    playerPanel.append(
      tabs,
      list,
      node(
        'p',
        'attention-note',
        '언급 횟수와 비중은 채널 편집·수집 특성의 영향을 받습니다. 호감도나 영향력 평가가 아닙니다.'
      )
    );
    section.append(playerPanel);
    root.append(section);
  }

  function sectionShell(title, description) {
    const section = node('section', 'section');
    const head = node('div', 'section-head');
    head.append(node('h2', '', title), node('p', '', description));
    section.append(head);
    return section;
  }
  function renderReport(run, root) {
    const report = run.report;
    if (!report) return;
    const overview = sectionShell(
      '분석 종합',
      '완료된 문서 요약과 명시된 근거를 함께 해석한 결과입니다.'
    );
    overview.append(
      node('p', 'report-summary', report.summary || '종합 요약이 아직 생성되지 않았습니다.')
    );
    root.append(overview);
    const topics = report.major_topics || [];
    if (topics.length) {
      const section = sectionShell(
        '핵심 주제와 의미',
        '가중치는 이 분석 스냅샷 안에서의 상대적 비중입니다.'
      );
      const cards = node('div', 'topic-cards');
      topics.forEach((item) => {
        const card = node('article', 'topic-card');
        const title = node('h3', '', item.name || '주제 미상');
        title.append(node('span', 'weight', percent(item.weight)));
        card.append(
          title,
          node('p', '', item.meaning || '의미 설명 없음'),
          evidenceLinks(item.evidence_doc_ids, run.id)
        );
        cards.append(card);
      });
      section.append(cards);
      root.append(section);
    }
    const relations = report.relationships || [];
    if (relations.length) {
      const section = sectionShell(
        '플레이어 관계',
        '메시지와 원문 요약에서 관찰된 관계입니다. 추론은 연결된 근거를 열어 확인하세요.'
      );
      const list = node('div', 'relations');
      relations.forEach((item) => {
        const row = node('article', 'relation-row');
        row.append(
          node('div', 'relation-entity', item.source || '출발점 미상'),
          node('div', 'arrow', '→'),
          node('div', 'relation-entity', item.target || '대상 미상')
        );
        const meaning = node('div', 'relation-meaning');
        meaning.append(
          node('span', 'relation-type', item.type || '관계'),
          document.createTextNode(item.meaning || '관계 설명 없음'),
          evidenceLinks(item.evidence_doc_ids, run.id)
        );
        row.append(meaning);
        list.append(row);
      });
      section.append(list);
      root.append(section);
    }
    renderStrategies(
      run,
      root,
      '국가 전략 흐름',
      report.country_strategies || [],
      '국가별 정책·투자·규제 신호를 자료 범위 안에서 종합합니다.'
    );
    renderStrategies(
      run,
      root,
      '기업 전략 흐름',
      report.company_strategies || [],
      '기업의 제품·제휴·투자·조직 움직임을 자료 범위 안에서 종합합니다.'
    );
    renderOutlooks(run, root, report.outlooks || {});
  }
  function renderStrategies(run, root, title, items, description) {
    if (!items.length) return;
    const section = sectionShell(title, description);
    const grid = node('div', 'strategy-grid');
    items.forEach((item) => {
      const card = node('article', 'strategy-card');
      card.append(
        node('h3', '', item.name || '대상 미상'),
        node('p', '', item.assessment || '평가 없음'),
        evidenceLinks(item.evidence_doc_ids, run.id)
      );
      grid.append(card);
    });
    section.append(grid);
    root.append(section);
  }
  function renderOutlooks(run, root, outlooks) {
    const buckets = [
      ['short', '단기', '0–3개월'],
      ['medium', '중기', '3–12개월'],
      ['long', '장기', '1–3년'],
    ];
    if (!buckets.some(([key]) => (outlooks[key] || []).length)) return;
    const section = sectionShell(
      '조건부 전망',
      '예측은 확정된 사실이 아닙니다. 가정과 관찰 신호가 달라지면 시나리오도 바뀝니다.'
    );
    const columns = node('div', 'horizons');
    buckets.forEach(([key, title, range]) => {
      const column = node('div', `horizon ${key}`);
      const head = node('div', 'horizon-head');
      head.append(node('strong', '', title), node('span', '', range));
      column.append(head);
      const items = outlooks[key] || [];
      if (!items.length) column.append(node('div', 'scenario', '아직 생성된 시나리오가 없습니다.'));
      items.forEach((item) => {
        const card = node('article', 'scenario');
        card.append(node('h3', '', item.scenario || '시나리오'));
        if (item.assessment) card.append(node('p', '', item.assessment));
        const dl = node('dl');
        [
          ['가정', item.assumptions],
          ['관찰 신호', item.signals],
          ['위험', item.risks],
        ].forEach(([label, value]) => {
          if (value == null || value === '' || (Array.isArray(value) && !value.length)) return;
          const rendered = Array.isArray(value) ? value.join('\n') : text(value);
          const group = node('div');
          group.append(node('dt', '', label), node('dd', '', rendered));
          dl.append(group);
        });
        card.append(dl, evidenceLinks(item.evidence_doc_ids, run.id));
        column.append(card);
      });
      columns.append(column);
    });
    section.append(columns);
    root.append(section);
  }

  function renderRun() {
    const run = state.run;
    if (!run) return emptyView();
    const root = node('div');
    const head = node('header', 'run-head');
    const info = node('div');
    info.append(
      statusPill(run.status, run.coverage),
      node('h2', '', `분석 스냅샷 ${dateTime(run.created_at)}`),
      node(
        'p',
        'run-meta',
        `범위 ID ${run.id} · 시작 ${dateTime(run.started_at)} · 완료 ${dateTime(run.completed_at)}`
      )
    );
    head.append(info);
    if (['paused', 'failed'].includes(run.status)) {
      const resume = node('button', 'resume', '중단 지점에서 재개');
      resume.type = 'button';
      resume.disabled = state.posting;
      resume.addEventListener('click', resumeRun);
      head.append(resume);
    }
    root.append(head);
    if (run.error) {
      const issue = node('p', 'coverage-note error', run.error);
      root.append(issue);
    }
    renderProgress(run, root);
    renderMetrics(run, root);
    renderReport(run, root);
    renderDocumentsShell(run, root);
    els.content.replaceChildren(root);
    renderRuns();
    updateStart();
  }

  function renderDocumentsShell(run, root) {
    const section = sectionShell(
      '문서별 요약과 근거',
      '원문 전체, 일부 발췌, 수집 실패 상태를 구분해 보여 줍니다. 분석 결론의 범위를 직접 확인할 수 있습니다.'
    );
    section.id = 'documents';
    const tools = node('div', 'docs-tools');
    const page = node('span', 'docs-page', '문서를 불러오는 중');
    tools.append(page);
    section.querySelector('.section-head').append(tools);
    const list = node('ol', 'docs-list');
    list.append(node('li', 'empty-block', '문서 목록을 불러오는 중입니다.'));
    const pager = node('div', 'pager');
    section.append(list, pager);
    root.append(section);
    loadDocuments(run.id, state.docsPage, list, pager, page);
  }
  function analysisSummary(analysis) {
    if (!analysis) return '';
    if (typeof analysis === 'string') return analysis;
    return text(
      analysis.summary || analysis.detailed_summary || analysis.meaning || analysis.assessment || ''
    );
  }
  function humanField(key) {
    return (
      {
        key_points: '핵심 내용',
        significance: '의미',
        meaning: '의미',
        entities: '주요 플레이어',
        topics: '주제',
        claims: '주요 주장',
        evidence: '근거',
        cautions: '주의점',
      }[key] || key.replaceAll('_', ' ')
    );
  }
  function valueText(value) {
    if (Array.isArray(value))
      return value
        .map((item) =>
          item && typeof item === 'object'
            ? text(
                item.text ||
                  item.name ||
                  item.scenario ||
                  Object.values(item).map(valueText).join(' · ')
              )
            : text(item)
        )
        .join('\n');
    if (value && typeof value === 'object')
      return Object.entries(value)
        .map(([k, v]) => `${humanField(k)}: ${valueText(v)}`)
        .join('\n');
    return text(value);
  }
  function renderDocument(doc, runId) {
    const li = node('li', 'doc');
    li.id = `doc-${encodeURIComponent(text(doc.doc_id))}`;
    li.dataset.docId = text(doc.doc_id);
    const head = node('div', 'doc-head');
    const titleBox = node('div');
    const title =
      (Array.isArray(doc.titles) ? doc.titles.find(Boolean) : '') ||
      doc.source_title ||
      doc.canonical_url ||
      '제목 없는 문서';
    titleBox.append(
      node('h3', 'doc-title', title),
      node('span', 'doc-id', `문서 ID ${doc.doc_id}`)
    );
    const sourceKind =
      doc.source_status === 'fetched'
        ? doc.source_truncated
          ? 'excerpt'
          : 'full'
        : doc.source_status === 'not_applicable'
          ? 'not_applicable'
          : doc.source_status === 'pending'
            ? 'pending'
            : 'failed';
    const badges = node('div', 'doc-badges');
    badges.append(
      node('span', `doc-badge ${sourceKind}`, SOURCE_STATUS[sourceKind] || '원문 상태 미상'),
      node(
        'span',
        `doc-badge ${doc.status}`,
        DOC_STATUS[doc.status] || doc.status || '분석 상태 미상'
      )
    );
    head.append(titleBox, badges);
    li.append(head);
    if (doc.message_evidence_count) {
      li.append(
        node(
          'p',
          'doc-summary',
          `날짜별 메시지 근거 ${number(doc.message_evidence_count)}개 보존 · 분석 입력 ${number(doc.message_evidence_selected)}개${doc.message_evidence_truncated ? ' (일부 발췌)' : ''}`
        )
      );
    }
    const summary = analysisSummary(doc.analysis) || doc.excerpt || '';
    if (summary) li.append(node('p', 'doc-summary', summary));
    if (doc.error) li.append(node('p', 'doc-summary error', doc.error));
    if (doc.analysis && typeof doc.analysis === 'object') {
      const details = node('details', 'doc-detail');
      details.append(node('summary', '', '상세 분석 보기'));
      const fields = node('div', 'doc-fields');
      Object.entries(doc.analysis)
        .filter(
          ([key, value]) =>
            !['summary', 'detailed_summary'].includes(key) && value != null && value !== ''
        )
        .forEach(([key, value]) => {
          const field = node('div', 'doc-field');
          field.append(node('strong', '', humanField(key)), node('p', '', valueText(value)));
          fields.append(field);
        });
      details.append(fields);
      li.append(details);
    }
    const links = node('div', 'doc-links');
    const urls = [doc.canonical_url, ...(Array.isArray(doc.variants) ? doc.variants : [])];
    [...new Set(urls.filter(Boolean))].forEach((url, index) => {
      const href = safeLink(url);
      if (!href) return;
      const link = node('a', '', index ? '다른 URL' : '원문 열기');
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      links.append(link);
    });
    const self = node('a', '', `근거 링크 복사·열기`);
    self.href = `/research?run=${encodeURIComponent(runId)}&document=${encodeURIComponent(doc.doc_id)}#documents`;
    links.append(self);
    if (links.childElementCount) li.append(links);
    return li;
  }
  async function loadDocuments(
    runId,
    pageNumber,
    list,
    pager,
    label,
    target = state.targetDocument
  ) {
    const runRequest = state.request;
    const docsRequest = ++state.docsRequest;
    try {
      const data = await requestJson(
        `/api/research/${encodeURIComponent(runId)}/documents?page=${pageNumber}&page_size=24`
      );
      if (
        runRequest !== state.request ||
        docsRequest !== state.docsRequest ||
        state.run?.id !== runId
      )
        return;
      state.docsPage = number(data.page) || 1;
      list.replaceChildren();
      (data.documents || []).forEach((doc) => list.append(renderDocument(doc, runId)));
      if (!list.childElementCount)
        list.append(node('li', 'empty-block', '이 스냅샷에 기록된 문서가 없습니다.'));
      label.textContent = `${number(data.total).toLocaleString('ko-KR')}개 문서 · ${state.docsPage}/${Math.max(1, number(data.total_pages))}쪽`;
      pager.replaceChildren();
      const prev = node('button', '', '이전');
      prev.type = 'button';
      prev.disabled = state.docsPage <= 1;
      prev.addEventListener('click', () =>
        loadDocuments(runId, state.docsPage - 1, list, pager, label, '')
      );
      const next = node('button', '', '다음');
      next.type = 'button';
      next.disabled = state.docsPage >= number(data.total_pages);
      next.addEventListener('click', () =>
        loadDocuments(runId, state.docsPage + 1, list, pager, label, '')
      );
      pager.append(prev, next);
      if (target) {
        const found = [...list.children].find((item) => item.dataset.docId === target);
        if (found) {
          state.targetDocument = '';
          found.classList.add('highlight');
          found.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else if (state.docsPage < number(data.total_pages)) {
          loadDocuments(runId, state.docsPage + 1, list, pager, label, target);
        } else {
          state.targetDocument = '';
          label.textContent += ' · 요청한 근거 문서를 찾지 못했습니다.';
        }
      }
    } catch (error) {
      if (runRequest !== state.request || docsRequest !== state.docsRequest) return;
      list.replaceChildren(node('li', 'empty-block error', error.message));
      label.textContent = '문서 목록 오류';
    }
  }
  async function requestJson(url, options = {}) {
    return request(url, options);
  }

  async function loadRuns({ select = true } = {}) {
    stopPolling();
    const requestId = ++state.request;
    try {
      const data = await requestJson('/api/research');
      if (requestId !== state.request) return;
      state.enabled = data.enabled !== false;
      state.runs = Array.isArray(data.runs) ? data.runs : [];
      updateStart();
      renderRuns();
      if (!select) return;
      if (!state.runs.length) {
        state.run = null;
        emptyView();
        return;
      }
      const params = new URLSearchParams(location.search);
      const wanted = params.get('run');
      state.targetDocument = params.get('document') || '';
      const selected = state.runs.find((run) => run.id === wanted) || state.runs[0];
      await selectRun(selected.id, true);
    } catch (error) {
      if (requestId !== state.request) return;
      state.enabled = false;
      state.runs = [];
      updateStart();
      Workspace.renderState(els.runs, 'error', '실행 목록 조회 실패', () => loadRuns());
      errorView(error.message, () => loadRuns());
    }
  }
  function queueRefresh(id, delay) {
    stopPolling();
    if (!document.hidden) state.timer = setTimeout(() => refreshRun(id), delay);
  }
  document.addEventListener('visibilitychange', () => {
    stopPolling();
    if (!document.hidden && state.run && ACTIVE.has(state.run.status))
      queueRefresh(state.run.id, 0);
  });
  async function selectRun(id, replace = false) {
    stopPolling();
    const requestId = ++state.request;
    state.docsPage = 1;
    const params = new URLSearchParams(location.search);
    state.targetDocument = params.get('run') === id ? params.get('document') || '' : '';
    updateUrl(id, state.targetDocument, replace);
    loading('분석 스냅샷을 불러오는 중입니다.');
    try {
      const run = await requestJson(`/api/research/${encodeURIComponent(id)}`);
      if (requestId !== state.request) return;
      state.run = run;
      renderRun();
      if (ACTIVE.has(run.status)) queueRefresh(id, 3000);
    } catch (error) {
      if (requestId !== state.request) return;
      errorView(error.message, () => selectRun(id, true));
    }
  }
  async function refreshRun(id) {
    const requestId = ++state.request;
    try {
      const run = await requestJson(`/api/research/${encodeURIComponent(id)}`);
      if (requestId !== state.request) return;
      state.run = run;
      const idx = state.runs.findIndex((item) => item.id === id);
      if (idx >= 0) state.runs[idx] = { ...state.runs[idx], ...run, report: null };
      renderRun();
      if (ACTIVE.has(run.status)) queueRefresh(id, 3000);
      else await loadRuns({ select: false });
    } catch (error) {
      if (requestId !== state.request) return;
      errorView(
        `${error.message} 분석 작업은 서버에서 계속될 수 있습니다. 새로고침해 상태를 다시 확인하세요.`,
        () => selectRun(id, true)
      );
    }
  }
  async function startRun() {
    if (els.start.disabled) return;
    state.posting = true;
    updateStart();
    try {
      const data = await requestJson('/api/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const run = data.run || data;
      if (!run?.id) throw new Error('분석 범위 ID를 받지 못했습니다.');
      state.runs = [run, ...state.runs.filter((item) => item.id !== run.id)];
      await selectRun(run.id, false);
    } catch (error) {
      errorView(error.message, () => loadRuns());
    } finally {
      state.posting = false;
      updateStart();
    }
  }
  async function resumeRun() {
    if (!state.run || state.posting) return;
    state.posting = true;
    renderRun();
    try {
      const data = await requestJson(`/api/research/${encodeURIComponent(state.run.id)}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      state.run = { ...state.run, ...(data.run || data) };
      renderRun();
      queueRefresh(state.run.id, 1500);
    } catch (error) {
      errorView(error.message, () => selectRun(state.run.id, true));
    } finally {
      state.posting = false;
      updateStart();
    }
  }
  async function openEvidence(docId) {
    if (!state.run) return;
    state.targetDocument = text(docId);
    updateUrl(state.run.id, state.targetDocument, false);
    const section = $('#documents');
    if (section) section.scrollIntoView({ behavior: 'smooth' });
    const list = section?.querySelector('.docs-list'),
      pager = section?.querySelector('.pager'),
      label = section?.querySelector('.docs-page');
    if (list && pager && label)
      await loadDocuments(state.run.id, 1, list, pager, label, state.targetDocument);
  }

  els.start.addEventListener('click', startRun);
  els.refresh.addEventListener('click', () => loadRuns());
  window.addEventListener('popstate', () => loadRuns());
  loadRuns();
})();
