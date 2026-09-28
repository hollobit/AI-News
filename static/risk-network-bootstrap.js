(() => {
  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = '/risk-network.css';
  document.head.append(style);
  const section = document.createElement('section');
  section.className = 'section risk-network-section';
  section.innerHTML =
    '<div class="section-head"><div><div class="eyebrow">GRAPHRAG / RISK RELATIONSHIP</div><h2>위험·조건부 시나리오 연결 지도</h2><p class="subtle">검토 통과된 위험과 조건부 시나리오를 공유 근거, 원문, GraphRAG 노드와 연결합니다. 가중치는 검토 우선순위와 유사 위험 연결을 반영하며 발생 확률이 아닙니다.</p></div><button id="risk-network-refresh" class="button">관계 지도 갱신</button></div><div id="risk-network-summary" class="risk-network-summary"></div><div id="risk-network" class="risk-network"><p class="empty">관계 지도를 불러오는 중입니다.</p></div><p id="risk-network-detail" class="risk-network-detail" aria-live="polite"></p>';
  const anchor = document.querySelector('.risk-method');
  if (anchor) anchor.after(section);
  const script = document.createElement('script');
  script.src = '/risk-network.js';
  script.onload = () => {
    // Let the paginated risk list paint before the corpus-level relationship
    // projection begins; the graph is intentionally a secondary view.
    setTimeout(() => window.RiskNetwork?.load(), 1200);
    document
      .querySelector('#risk-network-refresh')
      ?.addEventListener('click', () => window.RiskNetwork?.load());
    [
      'risk-search',
      'risk-sort',
      'risk-domain',
      'risk-severity',
      'risk-likelihood',
      'risk-status',
    ].forEach((id) =>
      document.getElementById(id)?.addEventListener('change', () => window.RiskNetwork?.load())
    );
  };
  document.head.append(script);
})();
