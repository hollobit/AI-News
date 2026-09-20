(() => {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const palette = {
    center: '#294b42',
    topic: '#d38a4a',
    news: '#5b8f80',
    reviewed: '#d8a744',
    line: '#a7bbb2',
    active: '#243f38',
    muted: '#708178'
  };

  const svgEl = (tag, attrs = {}) => {
    const element = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  };

  const text = (value, fallback = '') => String(value ?? fallback).replace(/\s+/g, ' ').trim();
  const reviewed = (item) => Boolean(item?.strategic_analysis || item?.base_analysis?.verified || item?.analysis_status === 'complete' || item?.analyses?.length);
  const itemKey = (item, index) => text(item?.item_id || item?.id || item?.url || `${item?.day || 'unknown'}-${index}`);

  function normalize(items, limit = 48) {
    const unique = new Map();
    (Array.isArray(items) ? items : []).forEach((item, index) => {
      const key = itemKey(item, index);
      if (!unique.has(key)) unique.set(key, item);
    });
    const values = [...unique.values()];
    values.sort((a, b) => Number(reviewed(b)) - Number(reviewed(a)) || text(b.published_at || b.day).localeCompare(text(a.published_at || a.day)));
    return values.slice(0, limit);
  }

  function topicFor(item) {
    return text(item?.topic_title || item?.topic || item?.content_type_title, '기타');
  }

  function create(container, items, options = {}) {
    if (!container) return;
    const data = normalize(items, options.limit || 48);
    container.replaceChildren();
    if (!data.length) {
      const empty = document.createElement('p');
      empty.className = 'news-network-empty';
      empty.textContent = options.empty || '현재 범위에서 연결할 뉴스가 없습니다.';
      container.append(empty);
      return;
    }

    const width = Math.max(320, Math.min(980, container.clientWidth || 720));
    const height = width < 560 ? 460 : 520;
    const cx = width / 2;
    const cy = height / 2;
    const inner = Math.min(width, height);
    const topicRadius = inner * (width < 560 ? .24 : .27);
    const newsRadius = inner * (width < 560 ? .39 : .40);
    const topics = [...new Set(data.map(topicFor))].slice(0, 12);
    const topicCounts = new Map(topics.map(topic => [topic, data.filter(item => topicFor(item) === topic).length]));
    const centerLabel = text(options.centerLabel, data.every(item => item.day === data[0].day) ? data[0].day : '최근 뉴스');
    const topicNodes = topics.map((name, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / Math.max(1, topics.length);
      return { id: `topic:${name}`, name, x: cx + Math.cos(angle) * topicRadius, y: cy + Math.sin(angle) * topicRadius, count: topicCounts.get(name) || 0 };
    });
    const newsNodes = data.map((item, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / Math.max(1, data.length);
      return { id: `news:${itemKey(item, index)}`, item, name: text(item.title, '제목 없는 뉴스'), x: cx + Math.cos(angle) * newsRadius, y: cy + Math.sin(angle) * newsRadius, topic: topicFor(item), reviewed: reviewed(item) };
    });
    const byId = new Map([...topicNodes, ...newsNodes].map(node => [node.id, node]));
    const links = [];
    topicNodes.forEach(topic => {
      links.push({ source: 'center', target: topic.id, kind: 'center-topic' });
      newsNodes.filter(news => news.topic === topic.name).forEach(news => links.push({ source: topic.id, target: news.id, kind: 'topic-news' }));
    });

    const shell = document.createElement('div');
    shell.className = 'news-network-shell';
    const header = document.createElement('div');
    header.className = 'news-network-header';
    const count = document.createElement('span');
    count.className = 'news-network-count';
    count.textContent = `${data.length}개 뉴스 연결 · 주제 ${topics.length}개${data.length < (items || []).length ? ` · 상위 ${data.length}개 표시` : ''}`;
    header.append(count);
    const legend = document.createElement('span');
    legend.className = 'news-network-legend';
    legend.textContent = '● 주제  ·  ● 뉴스  ·  ◎ 검토 완료';
    header.append(legend);
    shell.append(header);

    const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${centerLabel} 뉴스와 주제 연결망` });
    const title = svgEl('title');
    title.textContent = `${centerLabel} 뉴스 연결망`;
    svg.append(title);
    const description = svgEl('desc');
    description.textContent = '중앙 날짜에서 주제로 이어지고, 주제에서 날짜별 뉴스로 이어지는 연결망입니다. 선은 분류·공동 범위를 보여주며 인과관계를 뜻하지 않습니다.';
    svg.append(description);
    const defs = svgEl('defs');
    const gradient = svgEl('linearGradient', { id: `news-network-gradient-${Math.random().toString(36).slice(2)}`, x1: '0%', y1: '0%', x2: '100%', y2: '100%' });
    gradient.append(svgEl('stop', { offset: '0%', 'stop-color': palette.topic }), svgEl('stop', { offset: '100%', 'stop-color': palette.news }));
    defs.append(gradient);
    svg.append(defs);
    const lines = svgEl('g', { class: 'news-network-links' });
    const nodes = svgEl('g', { class: 'news-network-nodes' });
    svg.append(lines, nodes);

    const center = { id: 'center', x: cx, y: cy, name: centerLabel };
    const pathFor = (source, target, kind) => {
      const bend = kind === 'center-topic' ? 0 : ((source.x + target.x) / 2 < cx ? -1 : 1) * Math.min(28, inner * .035);
      const mx = (source.x + target.x) / 2;
      const my = (source.y + target.y) / 2 + bend;
      return `M ${source.x.toFixed(1)} ${source.y.toFixed(1)} Q ${mx.toFixed(1)} ${my.toFixed(1)} ${target.x.toFixed(1)} ${target.y.toFixed(1)}`;
    };
    links.forEach(link => {
      const source = link.source === 'center' ? center : byId.get(link.source);
      const target = byId.get(link.target);
      if (!source || !target) return;
      const path = svgEl('path', { d: pathFor(source, target, link.kind), fill: 'none', stroke: link.kind === 'center-topic' ? palette.line : `url(#${gradient.id})`, 'stroke-width': link.kind === 'center-topic' ? 1.2 : 1.5, opacity: .55, 'data-source': source.id, 'data-target': target.id });
      lines.append(path);
      link.path = path;
    });

    const detail = document.createElement('div');
    detail.className = 'news-network-detail';
    detail.setAttribute('aria-live', 'polite');
    detail.textContent = `${centerLabel}를 중심으로 주제와 뉴스가 연결되어 있습니다. 노드를 선택하면 제목·날짜·검토 상태를 확인합니다.`;
    const setActive = (id) => {
      const connected = new Set([id]);
      links.forEach(link => { if (link.source === id) connected.add(link.target); if (link.target === id) connected.add(link.source); });
      lines.querySelectorAll('path').forEach(path => { const active = path.dataset.source === id || path.dataset.target === id; path.classList.toggle('is-active', active); path.classList.toggle('is-muted', id && !active); });
      nodes.querySelectorAll('[data-node-id]').forEach(node => { node.classList.toggle('is-active', node.dataset.nodeId === id); node.classList.toggle('is-muted', Boolean(id) && !connected.has(node.dataset.nodeId)); });
    };
    const showDetail = (node) => {
      setActive(node.id);
      if (node.item) {
        detail.textContent = `${node.name} · ${node.item.day || '날짜 미상'} · ${node.reviewed ? '검토 완료' : '기본 기록'}`;
        detail.dataset.url = node.item.source_url || node.item.url || '';
      } else if (node.id !== 'center') {
        detail.textContent = `${node.name} · 연결 뉴스 ${node.count}건`;
        detail.dataset.url = '';
      } else {
        detail.textContent = `${centerLabel} · ${data.length}개 뉴스 · ${topics.length}개 주제`;
        detail.dataset.url = '';
      }
    };
    const addNode = (node, kind) => {
      const group = svgEl('g', { 'data-node-id': node.id, class: `news-network-node ${kind}`, tabindex: '0', role: 'button', 'aria-label': node.name });
      const radius = kind === 'center' ? Math.min(52, inner * .105) : kind === 'topic' ? Math.min(24, 13 + node.count * 1.2) : 7;
      if (node.reviewed) group.append(svgEl('circle', { cx: node.x, cy: node.y, r: radius + 4, fill: 'none', stroke: palette.reviewed, 'stroke-width': 3, opacity: .95 }));
      group.append(svgEl('circle', { cx: node.x, cy: node.y, r: radius, fill: kind === 'center' ? palette.center : kind === 'topic' ? palette.topic : palette.news }));
      const label = svgEl('text', { x: node.x, y: node.y + (kind === 'center' ? 4 : radius + 18), 'text-anchor': 'middle', class: kind === 'news' ? 'news-network-label news-only-label' : 'news-network-label' });
      label.textContent = kind === 'center' ? centerLabel : kind === 'topic' ? node.name.slice(0, 13) : '';
      if (kind !== 'news') group.append(label);
      if (kind === 'news') {
        const titleNode = svgEl('title'); titleNode.textContent = `${node.name} · ${node.item.day || '날짜 미상'} · ${node.reviewed ? '검토 완료' : '기본 기록'}`; group.append(titleNode);
      }
      group.addEventListener('mouseenter', () => showDetail(node));
      group.addEventListener('focus', () => showDetail(node));
      group.addEventListener('click', () => showDetail(node));
      group.addEventListener('mouseleave', () => setActive(''));
      group.addEventListener('blur', () => setActive(''));
      group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); showDetail(node); } });
      nodes.append(group);
    };
    addNode(center, 'center');
    topicNodes.forEach(node => addNode(node, 'topic'));
    newsNodes.forEach(node => addNode(node, 'news'));
    shell.append(svg, detail);
    container.append(shell);
  }

  window.NewsNetwork = { render: create };
})();
