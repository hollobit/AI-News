/* Load one immutable generation; data shards contain sanitized public fields. */
(() => {
  'use strict';
  const pending = new Map();
  let manifestPromise;
  const newsParts = new Map();
  async function json(path) {
    if (!pending.has(path)) pending.set(path, (async () => {
      const response = await fetch(path);
      if (!response.ok) throw Error('공개 자료 조회 실패 · ' + response.status);
      return response.json();
    })().catch(error => { pending.delete(path); throw error; }));
    return pending.get(path);
  }
  async function manifest() {
    if (!manifestPromise) manifestPromise = json('site-manifest.json').then(value => {
      if (value.schema_version !== 1) throw Error('공개 데이터 형식을 확인해 주세요.');
      return value;
    }).catch(error => { manifestPromise = null; throw error; });
    return manifestPromise;
  }
  async function bucket(id) {
    let value = 2166136261;
    for (const byte of new TextEncoder().encode(id)) value = Math.imul(value ^ byte, 16777619) >>> 0;
    return (value & 255).toString(16).padStart(2, '0');
  }
  async function news() {
    const m = await manifest();
    return (await json(m.news.index)).map(([id, title, day, topic, url, count, part]) => {
      newsParts.set(id, part);
      return { id, title, day, topic, url, analyses: Array.from({length: count}, () => ({})), _compact: true };
    });
  }
  async function articles(ids) {
    const m = await manifest();
    if (!newsParts.size) await news();
    const keys = ids.map(id => newsParts.get(id));
    const rows = (await Promise.all([...new Set(keys)].map(key => m.news.parts[key] ? json(m.news.parts[key]) : []))).flat();
    const wanted = new Set(ids);
    return rows.filter(row => wanted.has(row.id));
  }
  async function searchNews() {
    const m = await manifest();
    return json(m.news.search);
  }
  async function section(name) {
    const m = await manifest();
    if (!['papers', 'risks', 'risk_graph', 'wiki', 'observatory'].includes(name)) throw Error('알 수 없는 공개 자료');
    return json(m[name]);
  }
  async function graphView({id = '', q = '', layer = 'all', limit = 100, articleId = '', sourceUrl = '', paperId = ''}) {
    const m = await manifest(), g = m.graph;
    if (!id && paperId) id = 'source:paper:' + paperId;
    if (!id && (articleId || sourceUrl)) {
      const lookup = await json(g.lookup);
      id = lookup.news[articleId] || lookup.urls[sourceUrl] || 'missing-source';
    }
    const loaded = new Map(), edges = new Map();
    async function collect(ids) {
      const keys = await Promise.all(ids.map(bucket));
      const parts = await Promise.all([...new Set(keys)].map(key => g.parts[key] ? json(g.parts[key]) : {nodes: [], edges: []}));
      for (const part of parts) {
        for (const n of part.nodes) loaded.set(n.id, n);
        for (const e of part.edges) edges.set(e.id, e);
      }
    }
    let selectedIds, total, detail = null;
    if (id) {
      await collect([id]);
      const incident = [...edges.values()].filter(e => e.source === id || e.target === id);
      await collect(incident.map(e => e.source === id ? e.target : e.source));
      const allowed = incident.filter(e => layer === 'all' || e.layer === layer);
      const ids = new Set([id, ...allowed.flatMap(e => [e.source, e.target])]);
      selectedIds = [...loaded.values()].filter(n => ids.has(n.id)).sort((a, b) => Number(b.id === id) - Number(a.id === id) || a._order - b._order).map(n => n.id);
      total = selectedIds.length;
      const found = loaded.get(id);
      detail = found ? {...found, connections: incident.map(e => ({...e, other: loaded.get(e.source === id ? e.target : e.source)}))}
        : {id, type: 'source', title: '현재 연결된 검토 지식 없음', scope: '이 링크의 대상은 현재 공개 스냅샷에 없습니다.', connections: []};
    } else if (q || layer !== 'all') {
      const index = await json(g.index), query = q.trim().toLocaleLowerCase();
      selectedIds = index.filter(n => (layer === 'all' || n[2].includes(layer)) && (!query || n[1].toLocaleLowerCase().includes(query))).map(n => n[0]);
      total = selectedIds.length;
      await collect(selectedIds.slice(0, limit));
    } else {
      const bootstrap = await json(g.bootstrap);
      for (const n of bootstrap.nodes) loaded.set(n.id, n);
      for (const e of bootstrap.edges) edges.set(e.id, e);
      selectedIds = bootstrap.nodes.map(n => n.id);
      total = g.total;
    }
    selectedIds = selectedIds.slice(0, limit);
    const ids = new Set(selectedIds);
    const nodes = selectedIds.map(key => loaded.get(key)).filter(Boolean);
    return {...g.meta, exported_at: m.exported_at, nodes, edges: [...edges.values()].filter(e => ids.has(e.source) && ids.has(e.target) && (layer === 'all' || e.layer === layer)),
      detail, total, shown: nodes.length, selectedId: id};
  }
  window.PublicData = {manifest, news, articles, searchNews, section, graphView};
})();
