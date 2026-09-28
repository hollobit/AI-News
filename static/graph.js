(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const els = {
    form: $('#filters'),
    q: $('#q'),
    date: $('#date'),
    topic: $('#topic'),
    type: $('#content-type'),
    channel: $('#channel'),
    analyze: $('#analyze'),
    preview: $('#preview'),
    state: $('#state'),
    svg: $('#graph'),
    viewport: $('#viewport'),
    empty: $('#empty'),
    detail: $('#detail'),
    side: $('#side-scroll'),
    entitySearch: $('#entity-search'),
    typeFilters: $('#type-filters'),
    relationFilters: $('#relation-filters'),
    hops: $('#hops'),
    density: $('#density'),
    edgeLabels: $('#edge-labels'),
    graphStats: $('#graph-stats'),
    askForm: $('#ask-form'),
    question: $('#question'),
    askButton: $('#ask-button'),
    askScope: $('#ask-scope'),
    answer: $('#answer'),
  };
  const state = {
    controller: null,
    askController: null,
    timer: null,
    request: 0,
    pollDeadline: 0,
    input: null,
    analysis: null,
    integrated: null,
    graphMeta: null,
    graphResult: null,
    nodes: [],
    edges: [],
    activeTypes: new Set(),
    activeRelations: new Set(),
    typeKey: '',
    relationKey: '',
    entityQuery: '',
    selected: null,
    labelPreferenceSet: false,
    scale: 1,
    x: 0,
    y: 0,
    dragging: false,
    pointer: null,
    start: null,
  };
  const TYPE_COLORS = {
    Country: '#d76a18',
    Company: '#6e4bc4',
    Organization: '#6e4bc4',
    Person: '#b53e76',
    Institution: '#1769e0',
    Technology: '#087f75',
    Concept: '#b07500',
    Paper: '#7448b7',
    Product: '#39824c',
    Event: '#bd3a3a',
    Policy: '#334e78',
  };
  const TYPE_TITLES = {
    Country: '국가',
    Company: '기업',
    Organization: '조직',
    Person: '인물',
    Institution: '기관',
    Technology: '기술',
    Concept: '개념',
    Paper: '논문',
    Product: '제품',
    Event: '사건',
    Policy: '정책',
  };
  const NS = 'http://www.w3.org/2000/svg';
  const text = (value) => (value == null ? '' : String(value));
  const svgEl = (tag, attrs = {}) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  };
  const htmlEl = (tag, cls, value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value != null) node.textContent = text(value);
    return node;
  };
  const safeLink = (url) => {
    try {
      const parsed = new URL(url, location.origin);
      return /^https?:$/.test(parsed.protocol) ? parsed.href : '';
    } catch {
      return '';
    }
  };

  function queryString() {
    const params = new URLSearchParams({ date: els.date.value || 'all' });
    if (els.q.value.trim()) params.set('q', els.q.value.trim());
    if (els.topic.value) params.set('topic', els.topic.value);
    if (els.type.value) params.set('content_type', els.type.value);
    if (els.channel.value.trim()) params.set('channel', els.channel.value.trim());
    return params.toString();
  }
  function setStatus(label, error = false) {
    els.state.textContent = label;
    els.state.classList.toggle('error', error);
  }
  function stopPolling() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    if (state.controller) state.controller.abort();
    state.controller = null;
  }
  function evidenceRows() {
    return Array.isArray(state.graphResult?.evidence)
      ? state.graphResult.evidence
      : Array.isArray(state.analysis?.evidence)
        ? state.analysis.evidence
        : Array.isArray(state.input?.evidence)
          ? state.input.evidence
          : Array.isArray(state.input?.mentions)
            ? state.input.mentions.slice(0, 24)
            : [];
  }
  function updatePreview() {
    const input = state.input || {};
    const total = Number(input.total_available || input.mentions?.length || 0);
    const selected = Number(input.selected_count || input.evidence?.length || Math.min(total, 24));
    const duplicates = Number(input.duplicate_count || 0);
    if (!total)
      els.preview.textContent = '현재 범위에는 분석할 고유 메시지가 없습니다. 필터를 넓혀 주세요.';
    else {
      const omitted = Math.max(0, total - selected);
      els.preview.replaceChildren();
      const lead = htmlEl('span');
      lead.append(
        '고유 메시지 ',
        htmlEl('strong', '', total),
        '개 중 대표 근거 ',
        htmlEl('strong', '', selected),
        '개를 분석합니다.'
      );
      if (duplicates) lead.append(` 중복 ${duplicates}개는 제외했습니다.`);
      if (omitted) lead.append(` ${omitted}개는 표본 한도로 이번 분석에서 생략됩니다.`);
      if (input.selection?.max_text_chars)
        lead.append(
          ` 각 메시지는 최대 ${Number(input.selection.max_text_chars).toLocaleString('ko-KR')}자 발췌입니다.`
        );
      els.preview.append(lead);
    }
    const status = state.analysis?.status || 'not_analyzed';
    const enabled = state.analysis?.enabled !== false;
    els.analyze.disabled = !total || !enabled || ['queued', 'running', 'complete'].includes(status);
    els.analyze.textContent =
      status === 'complete'
        ? '분석 완료'
        : status === 'stale'
          ? '다시 분석'
          : ['queued', 'running'].includes(status)
            ? '분석 중'
            : '관계 분석 시작';
    if (!enabled) setStatus(state.analysis?.disabled_reason || '외부 분석 비활성화', true);
    else if (status === 'queued') setStatus('분석 대기 중');
    else if (status === 'running') setStatus('관계를 분석하는 중');
    else if (status === 'failed') setStatus(state.analysis?.error || '분석에 실패했습니다.', true);
    else if (status === 'complete') setStatus('분석 완료');
    else if (status === 'stale') setStatus('메시지가 바뀌어 재분석이 필요합니다.');
    else setStatus(state.nodes.length ? '통합 그래프 표시 중' : total ? '분석 전' : '입력 없음');
  }
  function populateSelect(select, rows, valueKey, titleKey, allTitle) {
    if (!Array.isArray(rows) || !rows.length || select.options.length > 1) return;
    const current = select.value;
    select.replaceChildren(new Option(allTitle, select === els.date ? 'all' : ''));
    rows.forEach((row) =>
      select.append(new Option(text(row[titleKey] || row[valueKey]), text(row[valueKey])))
    );
    select.value = current;
  }
  function renderFilterChips(container, values, active, kind) {
    container.replaceChildren();
    if (!values.length) {
      container.append(htmlEl('span', 'state', '분석 후 표시됩니다.'));
      return;
    }
    values.forEach((value) => {
      const label = htmlEl('label', `filter-chip ${kind === 'relation' ? 'relation' : ''}`);
      const input = htmlEl('input');
      input.type = 'checkbox';
      input.checked = active.has(value);
      input.value = value;
      const mark = htmlEl('i');
      mark.style.setProperty(
        '--chip',
        kind === 'type' ? TYPE_COLORS[value] || '#8793a6' : '#7089ad'
      );
      label.append(
        input,
        mark,
        text(kind === 'type' ? TYPE_TITLES[value] || value : value.replaceAll('_', ' '))
      );
      input.addEventListener('change', () => {
        if (input.checked) active.add(value);
        else active.delete(value);
        renderGraph();
      });
      container.append(label);
    });
  }
  function renderGraphControls() {
    const types = [...new Set(state.nodes.map((n) => n.type))].sort();
    const relations = [...new Set(state.edges.map((e) => e.relation))].sort();
    const typeKey = types.join('\0'),
      relationKey = relations.join('\0');
    if (typeKey !== state.typeKey) {
      state.typeKey = typeKey;
      state.activeTypes = new Set(types);
      renderFilterChips(els.typeFilters, types, state.activeTypes, 'type');
    }
    if (relationKey !== state.relationKey) {
      state.relationKey = relationKey;
      state.activeRelations = new Set(relations);
      renderFilterChips(els.relationFilters, relations, state.activeRelations, 'relation');
    }
  }
  function adaptResult(payload) {
    state.input = payload.input || {};
    state.analysis = payload.analysis || { status: 'not_analyzed', enabled: true };
    if (payload.integrated) state.integrated = payload.integrated;
    const result = payload.graph || state.integrated || state.analysis.result || state.analysis;
    state.graphResult = result;
    state.graphMeta = {
      coverage: result.coverage || payload.coverage || {},
      totals: result.totals || payload.totals || {},
      truncated: Boolean(result.truncated || payload.truncated),
      facets: result.facets || payload.facets || {},
    };
    state.nodes = Array.isArray(result.nodes)
      ? result.nodes.slice(0, 200).map((n, i) => ({
          ...n,
          id: text(n.id || `node-${i}`),
          name: text(n.name || n.label || n.title || n.id || `키워드 ${i + 1}`),
          type: text(n.type || n.category || 'entity'),
          summary: text(n.summary || n.description || ''),
          evidence_ids: Array.isArray(n.evidence_ids) ? n.evidence_ids.map(text) : [],
        }))
      : [];
    const ids = new Set(state.nodes.map((n) => n.id));
    state.edges = Array.isArray(result.edges)
      ? result.edges
          .slice(0, 400)
          .map((e, i) => ({
            ...e,
            id: text(e.id || `edge-${i}`),
            source: text(e.source?.id || e.source || e.from || e.source_id),
            target: text(e.target?.id || e.target || e.to || e.target_id),
            relation: text(e.relation || e.label || e.type || '관련'),
            meaning: text(e.meaning || e.description || ''),
            confidence:
              e.confidence === 'inferred' || e.inferred === true ? 'inferred' : 'attributed',
            evidence_ids: Array.isArray(e.evidence_ids) ? e.evidence_ids.map(text) : [],
          }))
          .filter((e) => ids.has(e.source) && ids.has(e.target))
      : [];
    if (!state.labelPreferenceSet && state.edges.length > 80) els.edgeLabels.checked = false;
    const inputDates = [
      ...new Set((state.input.mentions || []).map((row) => row.day).filter(Boolean)),
    ]
      .sort()
      .reverse()
      .map((date) => ({ date }));
    populateSelect(
      els.date,
      payload.dates || state.input.dates || inputDates,
      'date',
      'date',
      '모든 날짜'
    );
    populateSelect(els.topic, payload.topics || state.input.topics, 'id', 'title', '모든 주제');
    updatePreview();
    renderGraphControls();
    renderGraph();
    renderOverview(result);
    updateAskState();
  }
  async function load(options = {}) {
    stopPolling();
    if (!options.poll) {
      state.integrated = null;
      if (state.askController) {
        state.askController.abort();
        state.askController = null;
      }
    }
    const request = ++state.request;
    const controller = new AbortController();
    state.controller = controller;
    if (!options.poll) setStatus('입력 확인 중');
    try {
      const query = queryString();
      const requests = [fetch('/api/graph?' + query, { signal: controller.signal })];
      if (!options.poll)
        requests.push(fetch('/api/graph/integrated?' + query, { signal: controller.signal }));
      const responses = await Promise.all(requests);
      const response = responses[0];
      const data = await response.json();
      if (request !== state.request) return;
      if (!response.ok) throw new Error(data.error || '관계 데이터를 불러오지 못했습니다.');
      if (responses[1]?.ok) data.integrated = await responses[1].json();
      adaptResult(data);
      const status = data.analysis?.status;
      if (['queued', 'running'].includes(status)) {
        if (!state.pollDeadline) state.pollDeadline = Date.now() + 300000;
        if (state.pollDeadline && Date.now() >= state.pollDeadline) {
          setStatus('분석 시간이 5분을 넘었습니다. 잠시 뒤 다시 확인해 주세요.', true);
          els.analyze.disabled = false;
        } else state.timer = setTimeout(() => load({ poll: true }), 3000);
      } else state.pollDeadline = 0;
    } catch (error) {
      if (error.name !== 'AbortError' && request === state.request) {
        setStatus(error.message, true);
        els.analyze.disabled = true;
      }
    }
  }
  async function analyze() {
    stopPolling();
    const request = ++state.request;
    els.analyze.disabled = true;
    setStatus('분석 요청 중');
    const controller = new AbortController();
    state.controller = controller;
    try {
      const response = await fetch('/api/graph/analyze?' + queryString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        signal: controller.signal,
      });
      const data = await response.json();
      if (request !== state.request) return;
      if (!response.ok) throw new Error(data.error || '분석을 시작하지 못했습니다.');
      state.pollDeadline = Date.now() + 300000;
      const poll = async () => {
        if (request !== state.request) return;
        await load({ poll: true });
      };
      state.timer = setTimeout(poll, 1200);
    } catch (error) {
      if (error.name !== 'AbortError') {
        setStatus(error.message, true);
        els.analyze.disabled = false;
      }
    }
  }
  function visibleGraph() {
    let nodes = state.nodes.filter((node) => state.activeTypes.has(node.type));
    const allowed = new Set(nodes.map((n) => n.id));
    let edges = state.edges.filter(
      (edge) =>
        allowed.has(edge.source) &&
        allowed.has(edge.target) &&
        state.activeRelations.has(edge.relation)
    );
    const query = state.entityQuery.trim().toLocaleLowerCase('ko');
    let seeds = [];
    if (state.selected?.kind === 'node' && allowed.has(state.selected.item.id))
      seeds = [state.selected.item.id];
    else if (query)
      seeds = nodes
        .filter((node) => node.name.toLocaleLowerCase('ko').includes(query))
        .map((node) => node.id);
    if (seeds.length && els.hops.value !== 'all') {
      const visible = new Set(seeds);
      let frontier = new Set(seeds);
      const hops = Number(els.hops.value) || 1;
      for (let step = 0; step < hops; step++) {
        const next = new Set();
        edges.forEach((edge) => {
          if (frontier.has(edge.source)) next.add(edge.target);
          if (frontier.has(edge.target)) next.add(edge.source);
        });
        next.forEach((id) => visible.add(id));
        frontier = next;
      }
      nodes = nodes.filter((node) => visible.has(node.id));
      edges = edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target));
    }
    return { nodes, edges };
  }
  function layoutNodes(sourceNodes) {
    const count = sourceNodes.length;
    if (!count) return [];
    const degrees = new Map(sourceNodes.map((node) => [node.id, 0]));
    state.edges.forEach((edge) => {
      if (degrees.has(edge.source)) degrees.set(edge.source, degrees.get(edge.source) + 1);
      if (degrees.has(edge.target)) degrees.set(edge.target, degrees.get(edge.target) + 1);
    });
    const ordered = [...sourceNodes].sort(
      (a, b) =>
        (degrees.get(b.id) || 0) - (degrees.get(a.id) || 0) || a.name.localeCompare(b.name, 'ko')
    );
    const density = Number(els.density.value || 100) / 100;
    if (count <= 10) {
      const radius = Math.min(215, 85 + count * 10) * density;
      return ordered.map((node, pos) => {
        const angle = (pos / count) * Math.PI * 2 - Math.PI / 2;
        const degree = degrees.get(node.id) || 0;
        return {
          ...node,
          x: 450 + Math.cos(angle) * radius,
          y: 325 + Math.sin(angle) * radius,
          r: Math.max(19, Math.min(31, 19 + degree * 0.7)),
          degree,
        };
      });
    }
    const golden = Math.PI * (3 - Math.sqrt(5)),
      spread = count <= 20 ? 60 : count <= 40 ? 45 : count <= 80 ? 30 : 20;
    return ordered.map((node, index) => {
      const radius = Math.min(278, spread * Math.sqrt(index + 1)) * density;
      const angle = index * golden - Math.PI / 2;
      const degree = degrees.get(node.id) || 0;
      return {
        ...node,
        x: 450 + Math.cos(angle) * radius,
        y: 325 + Math.sin(angle) * radius * 0.92,
        r: Math.max(14, Math.min(29, 16 + degree * 0.55)),
        degree,
      };
    });
  }
  function edgePath(a, b) {
    const dx = b.x - a.x,
      dy = b.y - a.y,
      len = Math.max(1, Math.hypot(dx, dy));
    const sx = a.x + (dx / len) * (a.r + 2),
      sy = a.y + (dy / len) * (a.r + 2),
      ex = b.x - (dx / len) * (b.r + 8),
      ey = b.y - (dy / len) * (b.r + 8);
    return { d: `M ${sx} ${sy} L ${ex} ${ey}`, mx: (sx + ex) / 2, my: (sy + ey) / 2 };
  }
  function selectItem(kind, item) {
    const same = state.selected?.kind === kind && state.selected.item.id === item.id;
    state.selected = same ? null : { kind, item };
    renderGraph();
    if (state.selected) renderDetail(kind, item);
    else renderOverview(state.graphResult || {});
    updateAskState();
  }
  function renderGraph() {
    els.viewport.replaceChildren();
    const visible = visibleGraph();
    els.empty.hidden = visible.nodes.length > 0;
    if (!visible.nodes.length) {
      els.graphStats.textContent = state.nodes.length
        ? '표시 필터에 맞는 노드가 없습니다.'
        : '표시할 통합 그래프가 없습니다.';
      return;
    }
    const nodes = layoutNodes(visible.nodes),
      byId = new Map(nodes.map((n) => [n.id, n]));
    const edgeLayer = svgEl('g'),
      nodeLayer = svgEl('g');
    visible.edges.forEach((edge) => {
      const a = byId.get(edge.source),
        b = byId.get(edge.target);
      if (!a || !b) return;
      const p = edgePath(a, b);
      const selected = state.selected?.kind === 'edge' && state.selected.item.id === edge.id;
      const line = svgEl('path', {
        d: p.d,
        class: `edge ${edge.confidence === 'inferred' ? 'inferred' : ''} ${selected ? 'selected' : ''}`,
        'marker-end': `url(#arrow-${edge.confidence === 'inferred' ? 'inferred' : 'reported'})`,
      });
      const hit = svgEl('path', {
        d: p.d,
        class: 'edge-hit',
        tabindex: '0',
        role: 'button',
        'aria-label': `${a.name}에서 ${b.name}: ${edge.relation}`,
      });
      const choose = () => selectItem('edge', edge);
      hit.addEventListener('click', choose);
      hit.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          choose();
        }
      });
      const label = svgEl('text', { x: p.mx, y: p.my - 6, class: 'edge-label' });
      label.textContent = edge.relation.slice(0, 24);
      label.style.display = els.edgeLabels.checked ? '' : 'none';
      edgeLayer.append(line, hit, label);
    });
    nodes.forEach((node) => {
      const selected = state.selected?.kind === 'node' && state.selected.item.id === node.id;
      const g = svgEl('g', {
        class: `node ${selected ? 'selected' : ''}`,
        transform: `translate(${node.x} ${node.y})`,
        tabindex: '0',
        role: 'button',
        'data-type': node.type.toLowerCase(),
        'aria-label': `${node.name}, ${node.type}`,
      });
      g.append(svgEl('circle', { r: node.r }));
      const label = svgEl('text', { y: 4 });
      label.textContent = node.name.length > 13 ? node.name.slice(0, 12) + '…' : node.name;
      g.append(label);
      const choose = () => selectItem('node', node);
      g.addEventListener('click', choose);
      g.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          choose();
        }
      });
      nodeLayer.append(g);
    });
    els.viewport.append(edgeLayer, nodeLayer);
    applyTransform();
    const totals = state.graphMeta?.totals || {},
      coverage = state.graphMeta?.coverage || {};
    const totalNodes = Number(totals.nodes || state.nodes.length),
      totalEdges = Number(totals.edges || state.edges.length);
    const analyzed = coverage.completed_graph_analyses ?? coverage.analyzed_graphs;
    const archive = coverage.total_archive_messages ?? coverage.total_archive_documents;
    let label = `표시 ${visible.nodes.length}/${totalNodes} 노드 · ${visible.edges.length}/${totalEdges} 관계`;
    if (analyzed != null) label += ` · 통합 분석 ${analyzed}회`;
    if (archive != null) label += ` · URL 기록 ${Number(archive).toLocaleString('ko-KR')}건`;
    if (state.graphMeta?.truncated) label += ' · 표시 한도 적용';
    els.graphStats.textContent = label;
  }
  function evidenceList(ids, sourceRows) {
    const all = Array.isArray(sourceRows) ? sourceRows : evidenceRows(),
      wanted = new Set((ids || []).map(text));
    const rows = wanted.size ? all.filter((row) => wanted.has(text(row.id))) : all;
    const list = htmlEl('ol', 'evidence');
    rows.forEach((row, index) => {
      const li = htmlEl('li');
      if (sourceRows) {
        li.id = 'answer-source-' + (index + 1);
        li.tabIndex = -1;
      }
      li.append(
        htmlEl('time', '', row.day || row.published_at || '날짜 미상'),
        htmlEl('strong', '', row.title || '제목 없는 메시지')
      );
      const value = row.text || row.excerpt || '내용 없음';
      const body = htmlEl('p', '', value);
      li.append(body);
      if (Number.isInteger(row.passage_index))
        li.append(
          htmlEl(
            'small',
            'caveat',
            `보관 원문 문단 ${row.passage_index + 1} · 문자 ${row.char_start}–${row.char_end}`
          )
        );
      if (
        state.input?.selection?.max_text_chars &&
        value.length >= Number(state.input.selection.max_text_chars)
      )
        li.append(htmlEl('span', 'badge', '발췌 길이 제한 적용'));
      const links = htmlEl('div', 'links');
      const urls = [
        ...(Array.isArray(row.source_urls) ? row.source_urls : []),
        row.source_url,
      ].filter(Boolean);
      [...new Set(urls)].forEach((url, i) => {
        const href = safeLink(url);
        if (href) {
          const a = htmlEl('a', '', `원문 ${i + 1}`);
          a.href = href;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          links.append(a);
        }
      });
      const tg = safeLink(row.telegram_url);
      if (tg) {
        const a = htmlEl('a', '', 'Telegram');
        a.href = tg;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        links.append(a);
      }
      if (links.childElementCount) li.append(links);
      list.append(li);
    });
    if (!rows.length)
      list.append(
        htmlEl(
          'li',
          '',
          '이 관계에 연결된 메시지 근거가 없습니다. 해석을 사실로 단정할 수 없습니다.'
        )
      );
    return list;
  }
  function renderDetail(kind, item) {
    const wrap = htmlEl('div', 'detail');
    const isEdge = kind === 'edge';
    let ids = item.evidence_ids || [];
    if (isEdge) {
      const from = state.nodes.find((n) => n.id === item.source)?.name || item.source;
      const to = state.nodes.find((n) => n.id === item.target)?.name || item.target;
      wrap.append(htmlEl('h3', '', `${from} → ${to}`));
      const badges = htmlEl('div', 'badges');
      badges.append(
        htmlEl(
          'span',
          `badge ${item.confidence === 'inferred' ? 'inferred' : 'reported'}`,
          item.confidence === 'inferred' ? '분석에서 추론' : '메시지에 보고됨'
        ),
        htmlEl('span', 'badge', item.relation)
      );
      wrap.append(badges, htmlEl('p', '', item.meaning || '관계에 대한 추가 설명이 없습니다.'));
      if (item.confidence === 'inferred')
        wrap.append(
          htmlEl(
            'p',
            'caveat',
            '추론 관계입니다. 아래 메시지 근거에서 직접 진술된 사실과 구분해 검토하세요.'
          )
        );
    } else {
      wrap.append(htmlEl('h3', '', item.name));
      const badges = htmlEl('div', 'badges');
      badges.append(htmlEl('span', 'badge', item.type || 'entity'));
      wrap.append(
        badges,
        htmlEl('p', '', item.summary || '이 키워드에 대한 추가 설명이 없습니다.')
      );
    }
    wrap.append(evidenceList(ids));
    els.detail.replaceWith(wrap);
    wrap.id = 'detail';
    els.detail = wrap;
    els.side.scrollTop = 0;
  }
  function renderOverview(result = {}) {
    if (
      state.selected &&
      ((state.selected.kind === 'node' &&
        !state.nodes.some((n) => n.id === state.selected.item.id)) ||
        (state.selected.kind === 'edge' &&
          !state.edges.some((e) => e.id === state.selected.item.id)))
    )
      state.selected = null;
    if (state.selected) return renderDetail(state.selected.kind, state.selected.item);
    const root = htmlEl('div');
    const summaryValue =
      typeof result.summary === 'object'
        ? result.summary.text || result.summary.description || result.summary.message || ''
        : result.summary;
    if (summaryValue) {
      const summary = htmlEl('div', 'summary');
      summary.append(htmlEl('strong', '', '분석 요약 '), text(summaryValue));
      root.append(summary);
    }
    const list = htmlEl('ul', 'node-list');
    state.nodes.forEach((node) => {
      const li = htmlEl('li');
      const btn = htmlEl('button', '', node.name);
      btn.type = 'button';
      btn.addEventListener('click', () => selectItem('node', node));
      li.append(btn);
      list.append(li);
    });
    if (state.nodes.length) root.append(list);
    [...(result.cautions || []), ...(result.limitations || [])].forEach((c) => {
      const box = htmlEl('p', 'caveat', c);
      box.style.margin = '14px 20px';
      root.append(box);
    });
    const details = htmlEl('details', 'all-evidence');
    const summary = htmlEl(
      'summary',
      '',
      `그래프에 연결된 메시지 근거 ${evidenceRows().length}개 보기`
    );
    details.append(summary, evidenceList([]));
    root.append(details);
    els.side.replaceChildren(root);
    els.detail = root;
  }
  function updateAskState() {
    const node = state.selected?.kind === 'node' ? state.selected.item : null;
    els.askScope.textContent = node
      ? `선택한 “${node.name}” 주변 관계에서 우선 검색`
      : '현재 필터의 저장 근거 전체에서 검색';
    els.askButton.disabled = !state.nodes.length || state.analysis?.enabled === false;
    if (state.nodes.length && els.answer.dataset.empty === 'true')
      els.answer.querySelector('p').textContent =
        '질문을 입력하면 통합 그래프의 연결 관계와 Telegram 근거에서 답을 찾습니다.';
  }
  function renderAnswer(data) {
    const citations = new Map((data.evidence || []).map((item, index) => [item.id, index + 1]));
    const root = htmlEl('div');
    root.append(
      htmlEl('h3', '', data.answer ? 'GraphRAG 답변' : '답변을 찾지 못했습니다.'),
      htmlEl('p', '', data.answer || '현재 그래프와 연결된 근거에서 답을 찾지 못했습니다.')
    );
    if (Array.isArray(data.selected_nodes) || Array.isArray(data.selected_edges))
      root.append(
        htmlEl(
          'div',
          'badges',
          `검색된 부분 그래프 · 노드 ${(data.selected_nodes || []).length}개 · 관계 ${(data.selected_edges || []).length}개`
        )
      );
    if (Array.isArray(data.claims) && data.claims.length) {
      const list = htmlEl('ol', 'claims');
      data.claims.forEach((claim) => {
        const li = htmlEl('li', '', claim.text || claim);
        if (Array.isArray(claim.evidence_ids) && claim.evidence_ids.length) {
          const refs = htmlEl('span', 'claim-evidence');
          claim.evidence_ids.forEach((id) => {
            const number = citations.get(id);
            if (!number) return;
            const a = htmlEl('a', '', `[근거 ${number}] `);
            a.href = '#answer-source-' + number;
            a.addEventListener('click', (event) => {
              event.preventDefault();
              document.getElementById('answer-source-' + number)?.focus({ preventScroll: false });
            });
            refs.append(a);
          });
          li.append(refs);
        }
        list.append(li);
      });
      root.append(list);
    }
    const limitations = Array.isArray(data.limitations)
      ? data.limitations.join('\n')
      : text(data.limitations);
    if (limitations) root.append(htmlEl('div', 'limitations', limitations));
    const evidence = Array.isArray(data.evidence) ? data.evidence : [];
    if (evidence.length) {
      const details = htmlEl('details', 'all-evidence');
      details.open = true;
      details.append(
        htmlEl('summary', '', `답변 근거 ${evidence.length}개`),
        evidenceList([], evidence)
      );
      root.append(details);
    }
    if (data.enrichment)
      root.append(
        htmlEl(
          'p',
          'caveat',
          `추가 근거 보강: ${data.enrichment.status} · ${data.enrichment.note || data.enrichment.error || '공개 원문 추가 검토'} · /sources에서 수집 기록 확인`
        )
      );
    if (data.timing)
      root.append(
        htmlEl(
          'p',
          'caveat',
          `응답 ${((Number(data.timing.total_ms) || Number(data.timing.answer_ms) || 0) / 1000).toFixed(1)}초${data.timing.answer_cache_hit ? ' · 같은 질문과 근거의 저장 답변' : ''}`
        )
      );
    const wikiJob = data.wiki_job_id || data.job?.id;
    if (
      wikiJob &&
      data.claims?.length &&
      data.verification?.method === 'independent_evidence_review'
    ) {
      const save = htmlEl('button', 'ask-button', '검토된 질문을 위키에 저장');
      save.addEventListener('click', async () => {
        save.disabled = true;
        try {
          const response = await fetch('/api/wiki', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'archive_question', job_id: wikiJob }),
          });
          const value = await response.json();
          if (!response.ok) throw Error(value.error || '저장 실패');
          const a = htmlEl('a', '', '질문 위키 열기 · 원근거 재검토 후 공개');
          a.href = value.url;
          save.replaceWith(a);
        } catch (error) {
          save.textContent = error.message;
          save.disabled = false;
        }
      });
      root.append(save);
    }
    els.answer.replaceChildren(root);
    els.answer.dataset.empty = 'false';
  }
  async function askGraph() {
    const question = els.question.value.trim();
    if (question.length < 3) {
      els.answer.replaceChildren(
        htmlEl('h3', '', 'GraphRAG 답변'),
        htmlEl('p', '', '질문을 세 글자 이상 입력해 주세요.')
      );
      els.question.focus();
      return;
    }
    if (state.askController) state.askController.abort();
    const controller = new AbortController();
    state.askController = controller;
    els.askButton.disabled = true;
    els.askButton.textContent = '근거 검색 중';
    els.answer.replaceChildren(
      htmlEl('h3', '', 'GraphRAG 답변'),
      htmlEl('p', '', '현재 그래프에서 관련 노드와 메시지 근거를 찾고 있습니다.')
    );
    const nodeIds = state.selected?.kind === 'node' ? [state.selected.item.id] : [];
    const started = Date.now();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, Workspace.questionTimeout);
    const ticker = setInterval(() => {
      if (state.askController === controller)
        els.askButton.textContent = `답변 작성 중 · ${Math.floor((Date.now() - started) / 1000)}초`;
    }, 1000);
    try {
      const response = await fetch('/api/graph/ask?' + queryString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, node_ids: nodeIds }),
        signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '그래프 답변을 만들지 못했습니다.');
      if (state.askController === controller) renderAnswer(data);
      if (data.job) {
        let active = true;
        while (active && state.askController === controller) {
          await new Promise((resolve) => setTimeout(resolve, 2000));
          const response = await fetch(data.job.url, { signal: controller.signal });
          const job = await response.json();
          if (!response.ok) throw new Error(job.error || '분석 조회 실패');
          if (state.askController !== controller) break;
          if (job.result?.answer)
            renderAnswer({ ...job.result, wiki_job_id: job.status === 'complete' ? job.id : null });
          active = ['queued', 'running', 'preparing'].includes(job.status);
          if (job.error) els.answer.append(htmlEl('p', '', job.error));
          if (job.status === 'stale') els.answer.replaceChildren(htmlEl('p', '', job.error));
        }
      }
    } catch (error) {
      if (state.askController === controller && (timedOut || error.name !== 'AbortError'))
        els.answer.replaceChildren(
          htmlEl('h3', '', 'GraphRAG 답변'),
          htmlEl(
            'p',
            '',
            timedOut
              ? '답변 대기 시간이 길어 요청을 종료했습니다. 잠시 후 다시 시도해 주세요.'
              : error.message
          )
        );
    } finally {
      clearTimeout(timer);
      clearInterval(ticker);
      if (state.askController === controller) {
        state.askController = null;
        els.askButton.textContent = '근거에서 답 찾기';
        updateAskState();
      }
    }
  }
  function applyTransform() {
    els.viewport.setAttribute(
      'transform',
      `translate(${state.x} ${state.y}) scale(${state.scale})`
    );
  }
  function zoom(factor, cx = 450, cy = 325) {
    const old = state.scale,
      next = Math.max(0.55, Math.min(2.4, old * factor));
    state.x = cx - (cx - state.x) * (next / old);
    state.y = cy - (cy - state.y) * (next / old);
    state.scale = next;
    applyTransform();
  }
  els.svg.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      const rect = els.svg.getBoundingClientRect();
      zoom(
        e.deltaY < 0 ? 1.12 : 0.89,
        ((e.clientX - rect.left) * 900) / rect.width,
        ((e.clientY - rect.top) * 650) / rect.height
      );
    },
    { passive: false }
  );
  els.svg.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.node,.edge-hit')) return;
    state.dragging = true;
    state.pointer = e.pointerId;
    state.start = { x: e.clientX, y: e.clientY, ox: state.x, oy: state.y };
    els.svg.setPointerCapture(e.pointerId);
  });
  els.svg.addEventListener('pointermove', (e) => {
    if (!state.dragging || e.pointerId !== state.pointer) return;
    const rect = els.svg.getBoundingClientRect();
    state.x = state.start.ox + ((e.clientX - state.start.x) * 900) / rect.width;
    state.y = state.start.oy + ((e.clientY - state.start.y) * 650) / rect.height;
    applyTransform();
  });
  els.svg.addEventListener('pointerup', () => {
    state.dragging = false;
  });
  $('#zoom-in').addEventListener('click', () => zoom(1.2));
  $('#zoom-out').addEventListener('click', () => zoom(0.83));
  $('#zoom-reset').addEventListener('click', () => {
    state.scale = 1;
    state.x = 0;
    state.y = 0;
    state.selected = null;
    state.entityQuery = '';
    els.entitySearch.value = '';
    renderGraph();
    renderOverview(state.graphResult || {});
    updateAskState();
  });
  els.entitySearch.addEventListener('input', () => {
    state.entityQuery = els.entitySearch.value.trim();
    renderGraph();
    document.querySelectorAll('.node-list button').forEach((button) => {
      button.closest('li').hidden =
        !!state.entityQuery &&
        !button.textContent
          .toLocaleLowerCase('ko')
          .includes(state.entityQuery.toLocaleLowerCase('ko'));
    });
  });
  els.hops.addEventListener('change', renderGraph);
  els.density.addEventListener('input', renderGraph);
  els.edgeLabels.addEventListener('change', () => {
    state.labelPreferenceSet = true;
    renderGraph();
  });
  els.askForm.addEventListener('submit', (e) => {
    e.preventDefault();
    askGraph();
  });
  els.form.addEventListener('submit', (e) => {
    e.preventDefault();
    analyze();
  });
  let debounce;
  els.form.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.selected = null;
      load();
    }, 350);
  });
  els.form.addEventListener('change', () => {
    clearTimeout(debounce);
    state.selected = null;
    load();
  });
  const initial = new URLSearchParams(location.search);
  if (
    initial.has('date') &&
    initial.get('date') !== 'all' &&
    ![...els.date.options].some((option) => option.value === initial.get('date'))
  )
    els.date.append(new Option(initial.get('date'), initial.get('date')));
  ['q', 'date', 'topic', 'content_type', 'channel'].forEach((key) => {
    const el = key === 'content_type' ? els.type : els[key];
    if (el && initial.has(key)) el.value = initial.get(key);
  });
  load();
})();
