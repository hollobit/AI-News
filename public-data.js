/* Read a coherent immutable generation, recover expired shards, bound memory. */
(() => {
  'use strict';
  const inFlight = new Map(),
    cache = new Map();
  const maxBytes = 24 * 1024 * 1024,
    maxEntries = 48;
  let cacheBytes = 0,
    manifestPromise,
    current,
    refreshPromise;
  function remember(path, value) {
    const bytes = new TextEncoder().encode(JSON.stringify(value)).byteLength;
    if (bytes > maxBytes) return;
    cache.set(path, { value, bytes });
    cacheBytes += bytes;
    while (cache.size > maxEntries || cacheBytes > maxBytes) {
      const key = cache.keys().next().value;
      cacheBytes -= cache.get(key).bytes;
      cache.delete(key);
    }
  }
  async function json(path) {
    if (cache.has(path)) {
      const entry = cache.get(path);
      cache.delete(path);
      cache.set(path, entry);
      return entry.value;
    }
    if (!inFlight.has(path)) {
      const request = (async () => {
        const response = await fetch(path);
        if (!response.ok) {
          const error = Error('공개 자료 조회 실패 · ' + response.status);
          error.status = response.status;
          error.path = path;
          throw error;
        }
        const value = await response.json();
        remember(path, value);
        return value;
      })().finally(() => inFlight.delete(path));
      inFlight.set(path, request);
    }
    return inFlight.get(path);
  }
  async function readManifest() {
    const response = await fetch('site-manifest.json', { cache: 'no-store' });
    if (!response.ok) throw Error('공개 자료 목록 조회 실패 · ' + response.status);
    const value = await response.json();
    if (value.schema_version !== 1) throw Error('공개 데이터 형식을 확인해 주세요.');
    const changed = current && current.version !== value.version;
    current = value;
    if (changed && typeof dispatchEvent === 'function')
      dispatchEvent(new CustomEvent('public-data-updated'));
    return value;
  }
  async function manifest() {
    if (!manifestPromise)
      manifestPromise = readManifest().catch((error) => {
        manifestPromise = null;
        throw error;
      });
    return manifestPromise;
  }
  async function refresh() {
    if (!refreshPromise)
      refreshPromise = readManifest()
        .then((value) => {
          manifestPromise = Promise.resolve(value);
          return value;
        })
        .finally(() => {
          refreshPromise = null;
        });
    return refreshPromise;
  }
  async function generation(fn) {
    let m = await manifest();
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const value = await fn(m);
        if (current.version === m.version) return value;
        m = current;
      } catch (error) {
        if (
          attempt ||
          ![404, 410].includes(error.status) ||
          !/^public-data-[a-f0-9]{64}\.json$/.test(error.path || '')
        )
          throw error;
        const next = await refresh();
        if (next.version === m.version) throw error;
        m = next;
      }
    }
    throw Error('공개 자료가 갱신되었습니다. 다시 조회해 주세요.');
  }
  function bucket(id) {
    let value = 2166136261;
    for (const byte of new TextEncoder().encode(id))
      value = Math.imul(value ^ byte, 16777619) >>> 0;
    return (value & 255).toString(16).padStart(2, '0');
  }
  function news() {
    return generation(async (m) =>
      (await json(m.news.index)).map(([id, title, day, topic, url, count]) => ({
        id,
        title,
        day,
        topic,
        url,
        analyses: Array.from({ length: count }, () => ({})),
        _compact: true,
      }))
    );
  }
  function articles(ids) {
    return generation(async (m) => {
      if (m.news.bootstrap && ids.every((id) => m.news.bootstrap_ids.includes(id))) {
        const wanted = new Set(ids);
        return (await json(m.news.bootstrap)).filter((row) => wanted.has(row.id));
      }
      const index = await json(m.news.index),
        wanted = new Set(ids);
      const keys = [...new Set(index.filter((row) => wanted.has(row[0])).map((row) => row[6]))];
      const rows = [];
      for (let offset = 0; offset < keys.length; offset += 4)
        rows.push(
          ...(
            await Promise.all(keys.slice(offset, offset + 4).map((key) => json(m.news.parts[key])))
          ).flat()
        );
      return rows.filter((row) => wanted.has(row.id));
    });
  }
  function searchNews() {
    return generation((m) => json(m.news.search));
  }
  function section(name) {
    if (!['papers', 'risks', 'risk_graph', 'wiki', 'observatory'].includes(name))
      throw Error('알 수 없는 공개 자료');
    return generation((m) => json(m[name]));
  }
  function graphView(options = {}) {
    return generation((m) => graph(m, options));
  }
  async function graph(
    m,
    { id = '', q = '', layer = 'all', limit = 100, articleId = '', sourceUrl = '', paperId = '' }
  ) {
    const g = m.graph;
    limit = Math.max(1, Math.min(500, Number(limit) || 100));
    if (!id && paperId) id = 'source:paper:' + paperId;
    if (!id && (articleId || sourceUrl)) {
      const lookup = await json(g.lookup);
      id = lookup.news[articleId] || lookup.urls[sourceUrl] || 'missing-source';
    }
    const loaded = new Map(),
      edges = new Map(),
      labels = new Map();
    async function collect(ids) {
      const keys = [...new Set(ids.map(bucket))];
      // Bound concurrent fetches even when a user asks for a large page.
      for (let offset = 0; offset < keys.length; offset += 4) {
        const parts = await Promise.all(
          keys
            .slice(offset, offset + 4)
            .map((key) => (g.parts[key] ? json(g.parts[key]) : { nodes: [], edges: [] }))
        );
        for (const part of parts) {
          for (const n of part.nodes) {
            loaded.set(n.id, n);
            labels.set(n.id, n);
          }
          for (const [key, n] of Object.entries(part.neighbors || {}))
            if (!labels.has(key)) labels.set(key, n);
          for (const e of part.edges) edges.set(e.id, e);
        }
      }
    }
    let selectedIds,
      total,
      detail = null;
    if (id) {
      await collect([id]);
      const incident = [...edges.values()].filter(
        (e) => (e.source === id || e.target === id) && (layer === 'all' || e.layer === layer)
      );
      const adjacent = [...new Set(incident.map((e) => (e.source === id ? e.target : e.source)))];
      const order =
        g.order && adjacent.length && limit > 1
          ? new Map((await json(g.order)).map((key, i) => [key, i]))
          : null;
      // Compatibility with the immediately preceding shard schema.
      if (adjacent.some((key) => !labels.has(key))) {
        const index = await json(g.index);
        index.forEach((n, i) => {
          if (!labels.has(n[0])) labels.set(n[0], { id: n[0], title: n[1], _order: i });
        });
      }
      selectedIds = [
        id,
        ...adjacent
          .filter((key) => key !== id)
          .sort(
            (a, b) =>
              (order?.get(a) ?? labels.get(a)?._order ?? Infinity) -
              (order?.get(b) ?? labels.get(b)?._order ?? Infinity)
          ),
      ];
      total = loaded.has(id) ? selectedIds.length : 0;
      selectedIds = selectedIds.slice(0, limit);
      await collect(selectedIds.filter((key) => !loaded.has(key)));
      const found = loaded.get(id);
      detail = found
        ? {
            ...found,
            connections: incident
              .map((e) => ({
                ...e,
                other:
                  loaded.get(e.source === id ? e.target : e.source) ||
                  labels.get(e.source === id ? e.target : e.source),
              }))
              .filter((e) => e.other),
          }
        : {
            id,
            type: 'source',
            title: '현재 연결된 검토 지식 없음',
            scope: '이 링크의 대상은 현재 공개 스냅샷에 없습니다.',
            connections: [],
          };
    } else if (q || layer !== 'all') {
      const index = await json(g.index),
        query = q.trim().toLocaleLowerCase();
      selectedIds = index
        .filter(
          (n) =>
            (layer === 'all' || n[2].includes(layer)) &&
            (!query || n[1].toLocaleLowerCase().includes(query))
        )
        .map((n) => n[0]);
      total = selectedIds.length;
      await collect(selectedIds.slice(0, limit));
    } else {
      const bootstrap = await json(g.bootstrap);
      for (const n of bootstrap.nodes) loaded.set(n.id, n);
      for (const e of bootstrap.edges) edges.set(e.id, e);
      selectedIds = bootstrap.nodes.map((n) => n.id);
      total = g.total;
    }
    selectedIds = selectedIds.slice(0, limit);
    const ids = new Set(selectedIds),
      nodes = selectedIds.map((key) => loaded.get(key)).filter(Boolean);
    return {
      ...g.meta,
      exported_at: m.exported_at,
      nodes,
      edges: [...edges.values()].filter(
        (e) => ids.has(e.source) && ids.has(e.target) && (layer === 'all' || e.layer === layer)
      ),
      detail,
      total,
      shown: nodes.length,
      selectedId: id,
    };
  }
  window.PublicData = { manifest, news, articles, searchNews, section, graphView };
})();
