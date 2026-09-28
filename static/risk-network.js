(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? '');
  const fmt = (v) => Number(v || 0).toLocaleString('ko-KR');
  let controller;

  function render(data) {
    const box = $('#risk-network');
    const summary = $('#risk-network-summary');
    const nodes = data.nodes || [], edges = data.edges || [];
    summary.replaceChildren(...[
      ['위험·시나리오', data.coverage?.risks], ['원문 근거', data.coverage?.source_documents],
      ['GraphRAG 노드', data.coverage?.graphrag_nodes], ['관계선', data.coverage?.relationships]
    ].map(([label, value]) => { const e = document.createElement('div'); e.innerHTML = `<span>${label}</span><strong>${fmt(value)}</strong>`; return e; }));
    box.replaceChildren();
    if (!nodes.length) { box.append(Object.assign(document.createElement('p'), { className: 'empty', textContent: '현재 필터에 연결되는 검토 근거가 없습니다.' })); return; }
    const width = 1100, height = 650, cx = width / 2, cy = height / 2;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', '위험 및 조건부 시나리오 GraphRAG 관계 지도');
    const riskNodes = nodes.filter(n => n.kind === 'risk'), others = nodes.filter(n => n.kind !== 'risk');
    const positions = new Map();
    riskNodes.forEach((n, i) => { const a = (i / Math.max(1, riskNodes.length)) * Math.PI * 2 - Math.PI / 2; positions.set(n.id, { x: cx + Math.cos(a) * 210, y: cy + Math.sin(a) * 190 }); });
    others.forEach((n, i) => { const a = (i / Math.max(1, others.length)) * Math.PI * 2 - Math.PI / 2; positions.set(n.id, { x: cx + Math.cos(a) * 420, y: cy + Math.sin(a) * 275 }); });
    const edgeLayer = document.createElementNS(svg.namespaceURI, 'g'); edgeLayer.setAttribute('class', 'risk-network-edges'); svg.append(edgeLayer);
    const nodeLayer = document.createElementNS(svg.namespaceURI, 'g'); nodeLayer.setAttribute('class', 'risk-network-nodes'); svg.append(nodeLayer);
    const edgeEls = [];
    edges.forEach(edge => { const a = positions.get(edge.source), b = positions.get(edge.target); if (!a || !b) return;
      const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x} ${b.y}`); path.setAttribute('class', `risk-edge ${edge.relation === '공유 검토 근거 · 유사성' ? 'similarity' : ''}`); path.dataset.source = edge.source; path.dataset.target = edge.target;
      const title = document.createElementNS(svg.namespaceURI, 'title'); title.textContent = `${edge.relation}: ${edge.meaning || ''}${edge.similarity ? ` · 유사성 ${edge.similarity}` : ''}`; path.append(title); edgeLayer.append(path); edgeEls.push(path);
    });
    const nodeEls = [];
    nodes.forEach(n => { const p = positions.get(n.id); if (!p) return; const g = document.createElementNS(svg.namespaceURI, 'g'); g.setAttribute('class', `risk-node risk-node-${n.kind}`); g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button'); g.setAttribute('aria-label', `${n.label}${n.kind === 'risk' ? `, 그래프 가중치 ${n.weight ?? 0}` : ''}`); g.dataset.id = n.id; g.setAttribute('transform', `translate(${p.x} ${p.y})`);
      const r = n.kind === 'risk' ? 13 + Math.sqrt(Number(n.weight || 0)) * 1.35 : n.kind === 'evidence' ? 8 : 10; const c = document.createElementNS(svg.namespaceURI, 'circle'); c.setAttribute('r', r); g.append(c);
      const label = document.createElementNS(svg.namespaceURI, 'text'); label.setAttribute('y', r + 15); label.textContent = esc(n.label).slice(0, 24); g.append(label);
      const title = document.createElementNS(svg.namespaceURI, 'title'); title.textContent = n.kind === 'risk' ? `${n.label} · 가중치 ${n.weight ?? 0} · 유사 위험 ${n.similar_risk_count ?? 0}개` : n.label; g.prepend(title);
      const activate = () => { nodeEls.forEach(item => item.classList.remove('active')); edgeEls.forEach(item => item.classList.remove('active')); g.classList.add('active'); edgeEls.filter(e => e.dataset.source === n.id || e.dataset.target === n.id).forEach(e => e.classList.add('active')); const detail = $('#risk-network-detail'); detail.textContent = n.kind === 'risk' ? `${n.label} · 그래프 가중치 ${n.weight ?? 0} · 유사 위험 ${n.similar_risk_count ?? 0}개 · 공유 근거 ${n.shared_evidence_count ?? 0}개. ${n.weight_explanation || ''}` : `${n.label} · ${n.summary || '연결된 검토 근거'}`; };
      g.addEventListener('mouseenter', activate); g.addEventListener('focus', activate); g.addEventListener('click', activate); g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); } }); nodeLayer.append(g); nodeEls.push(g);
    });
    box.append(svg);
    $('#risk-network-detail').textContent = data.method?.causality || '관계선을 선택하면 연결 근거가 표시됩니다.';
  }
  async function load() { controller?.abort(); controller = new AbortController(); const params = new URLSearchParams(); ['q','domain','severity','likelihood','status'].forEach(key => { const id = key === 'q' ? 'risk-search' : `risk-${key}`; const value = document.getElementById(id)?.value; if (value && value !== 'all') params.set(key, value); }); params.set('risk_limit', '48'); try { const response = await fetch('/api/risks/graph?' + params, { signal: controller.signal }); const data = await response.json(); if (!response.ok) throw new Error(data.error || '관계 지도를 불러오지 못했습니다.'); render(data); } catch (error) { if (error.name !== 'AbortError') { $('#risk-network').replaceChildren(Object.assign(document.createElement('p'), { className: 'empty', textContent: error.message })); } } }
  window.RiskNetwork = { load, render };
})();
