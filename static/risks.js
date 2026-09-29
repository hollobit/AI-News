(() => {
  'use strict';
  const $ = (s) => document.querySelector(s),
    node = (tag, cls = '', value = '') => {
      const e = document.createElement(tag);
      e.className = cls;
      e.textContent = value;
      return e;
    },
    num = (n) => Number(n || 0).toLocaleString('ko-KR');
  const grades = { unknown: '미상', low: '낮음', moderate: '보통', high: '높음', critical: '심각' },
    domains = {
      economy: '국가경제',
      security: '국가안보',
      industry: '산업',
      exports: '수출',
      social: '사회적 문제',
      life: '생활',
      education: '교육',
    },
    horizons = {
      unknown: '기간 판단 불가',
      '0-3mo': '0–3개월',
      '3-12mo': '3–12개월',
      '12-36mo': '1–3년',
    };
  let page = 1,
    reviewPage = 1,
    reviewOpen = false,
    controller,
    requestId = 0,
    graphPromise;
  function text(v) {
    if (v == null) return '';
    if (typeof v !== 'object') return String(v);
    if (Array.isArray(v)) return v.map(text).join(' · ');
    return (
      v.explanation ||
      v.description ||
      v.text ||
      v.detail ||
      Object.entries(v)
        .map(([k, x]) => k + ': ' + text(x))
        .join(' · ')
    );
  }
  function link(label, url) {
    try {
      const u = new URL(url, location.origin);
      if (!['http:', 'https:'].includes(u.protocol)) return node('span', '', label);
      const e = node('a', 'text-link', label);
      e.href = u.href;
      e.target = '_blank';
      e.rel = 'noopener noreferrer';
      return e;
    } catch (_) {
      return node('span', '', label);
    }
  }
  async function api(url, signal) {
    return Workspace.request(url, { signal });
  }
  function riskGraph() {
    if (!graphPromise) graphPromise = api('/api/risks/graph?risk_limit=2000&edge_limit=12000');
    return graphPromise;
  }
  function scoreLabel(p) {
    if (!p || p.status === 'unknown')
      return p?.range ? `미평가 · ${p.range[0]}–${p.range[1]}점 범위` : '미평가';
    if (p.status === 'needs_review')
      return p.range ? `${p.range[0]}–${p.range[1]}점 · 재검토 필요` : '재검토 필요';
    if (p.status === 'partial' && p.range) return `${p.range[0]}–${p.range[1]}점 · 부분 평가`;
    return p.score == null ? '미평가' : `${p.score} / 100`;
  }
  function priorityBlock(r) {
    const p = r.priority || {},
      box = node('div');
    const main = node('div', 'priority-score');
    main.append(
      node(
        'span',
        '',
        r.lifecycle === 'withdrawn_source_mismatch'
          ? '철회 기록의 검토 지수'
          : r.lifecycle === 'superseded'
            ? '대체된 기록의 검토 지수'
            : '검토 우선순위'
      ),
      node('strong', '', scoreLabel(p))
    );
    box.append(
      main,
      node(
        'div',
        'risk-score-parts',
        `현재 검토점수 ${p.current_score == null ? '미평가' : num(p.current_score)} · 미래 검토점수 ${p.future_attention_score == null ? '미평가' : num(p.future_attention_score)}`
      )
    );
    return box;
  }
  const route = Workspace.routeState(
    {
      q: 'risk-search',
      sort: 'risk-sort',
      status: 'risk-status',
      domain: 'risk-domain',
      severity: 'risk-severity',
      likelihood: 'risk-likelihood',
    },
    { page: 1, pending_page: 1, risk: '' }
  );
  function restoreRoute() {
    const saved = route.read();
    page = Math.max(1, Number(saved.page) || 1);
    reviewPage = Math.max(1, Number(saved.pending_page) || 1);
    return saved;
  }
  restoreRoute();
  let restoring = false;
  function query() {
    const q = new URLSearchParams({
      view: 'page',
      page: String(page),
      page_size: '12',
      pending_page: String(reviewPage),
      pending_page_size: '12',
      sort: $('#risk-sort').value,
      status: $('#risk-status').value,
    });
    [
      ['q', 'risk-search'],
      ['domain', 'risk-domain'],
      ['severity', 'risk-severity'],
      ['likelihood', 'risk-likelihood'],
    ].forEach(([key, id]) => {
      if ($('#' + id).value) q.set(key, $('#' + id).value);
    });
    return q;
  }
  function compact(r) {
    const card = node('article', 'risk-row');
    if (r.lifecycle === 'withdrawn_source_mismatch')
      card.append(node('p', 'pill amber', '원문 연결 오류로 철회 · 활성 순위에서 제외'));
    if (r.lifecycle === 'superseded')
      card.append(node('p', 'pill', '후속 검증 평가로 대체 · 과거 기록'));
    if (r.resolution)
      card.append(
        node(
          'p',
          'subtle',
          r.resolution.verification_status === 'resolved'
            ? '후속 독립 검토 통과 · 기존 기록 보존'
            : '후속 정정 검토 대기 · 기존 기록 보존'
        )
      );
    card.append(priorityBlock(r));
    const h = node('h3'),
      b = node('button', 'risk-title', r.title);
    b.addEventListener('click', () => detail(r.id));
    h.append(b);
    card.append(
      h,
      node(
        'span',
        'pill' + (r.status === 'needs_review' ? ' amber' : ' green'),
        r.status === 'needs_review' ? '근거 재검토 필요' : '근거 검토 완료'
      ),
      node(
        'p',
        '',
        `현재 ${grades[r.current_severity] || '미상'} · 미래 ${grades[r.future_likelihood] || '미상'} / ${horizons[r.horizon] || r.horizon || '기간 미상'}`
      )
    );
    const tags = node('div', 'impact-tags');
    (r.impact_domains || []).forEach((d) => tags.append(node('span', 'pill', domains[d] || d)));
    card.append(
      tags,
      node(
        'p',
        'subtle',
        `분석 ${(r.analysis_at || '미상').slice(0, 10)} · 원문 ${(r.evidence_latest_date || '미상').slice(0, 10)} · 근거 ${num(r.evidence_count)}개`
      )
    );
    if (r.date_status && r.date_status !== 'recent')
      card.append(
        node(
          'p',
          'subtle',
          `원문 최신성: ${{ stale: '30일 초과', unknown: '날짜 미상', future_dated: '미래 날짜' }[r.date_status] || r.date_status}`
        )
      );
    const button = node('button', 'button', '판단 근거·상세 보기');
    button.addEventListener('click', () => detail(r.id));
    card.append(button);
    return card;
  }
  function render(data) {
    const c = data.coverage || {};
    $('#risk-coverage').replaceChildren(
      ...[
        ['근거 검토 완료 분석', c.assessed],
        ['검토 필요 분석', c.needs_review_workflows ?? c.needs_review],
        ['위험 미평가 분석', c.unassessed],
        ['현재 원문 재검토 항목', c.source_review_required],
        ['원문 오류 철회', c.withdrawn],
        ['후속 평가로 대체', c.superseded],
        ['이력에 보존된 원문 변경', c.historical_source_review_required],
        ['후속 검토 대기', c.resolution_pending],
        ['후속 검토 통과 정정', c.resolved],
        ['철회·정정 이력', c.history_total],
      ].map(([label, value]) => {
        const e = node('div');
        e.append(
          node('span', '', label),
          node('strong', '', value == null ? '집계 미제공' : num(value))
        );
        return e;
      })
    );
    renderMethod(data.scoring_method);
    $('#risk-result-count').textContent =
      `조건에 맞는 위험 항목 ${num(data.total)}개 · 미평가는 안전하거나 위험이 없다는 뜻이 아닙니다.`;
    $('#risk-list').replaceChildren(...(data.items || []).map(compact));
    if (!data.items?.length)
      $('#risk-list').append(
        node(
          'div',
          'empty',
          '현재 조건에 표시할 위험 항목이 없습니다. 미평가·검토 필요 분석은 위 집계에서 별도로 확인하세요.'
        )
      );
    const pagination = data.pagination || {};
    const totalPages = pagination.total_pages || Math.ceil(data.total / 12);
    $('#risk-page-number').textContent = totalPages
      ? `${pagination.page || page} / ${totalPages} 페이지`
      : '0개';
    $('#risk-prev').disabled = page <= 1;
    $('#risk-next').disabled = page >= totalPages;
    const pending = $('#risk-pending');
    pending.replaceChildren();
    const rp = data.review_pagination || data.needs_review_pagination || {};
    if (data.needs_review?.length || rp.total) {
      const d = node('details', 'risk-method');
      d.open = reviewOpen;
      d.addEventListener('toggle', () => {
        reviewOpen = d.open;
      });
      d.append(
        node(
          'summary',
          '',
          `위험 항목으로 확정하지 않은 검토 필요 분석 ${num(rp.total ?? data.needs_review.length)}개`
        )
      );
      (data.needs_review || []).forEach((r) =>
        d.append(node('p', '', text(r.issues) || '근거 검토 필요'))
      );
      const nav = node('div', 'risk-pagination'),
        prev = node('button', 'button', '검토 목록 이전'),
        next = node('button', 'button', '검토 목록 다음');
      prev.disabled = (rp.page || reviewPage) <= 1;
      next.disabled = (rp.page || reviewPage) >= (rp.total_pages || 1);
      prev.addEventListener('click', () => {
        reviewPage--;
        reviewOpen = true;
        load();
      });
      next.addEventListener('click', () => {
        reviewPage++;
        reviewOpen = true;
        load();
      });
      nav.append(
        prev,
        node('span', '', `${rp.page || reviewPage} / ${rp.total_pages || 1} 페이지`),
        next
      );
      d.append(nav);
      pending.append(d);
    }
  }
  function renderMethod(method) {
    const area = $('#risk-scoring-method');
    area.replaceChildren();
    if (!method || typeof method === 'string') {
      area.textContent = method || '점수 기준을 불러오는 중입니다.';
      return;
    }
    area.append(node('p', '', method.formula || ''));
    function table(title, values, labelMap) {
      if (!values || typeof values !== 'object') return;
      area.append(node('h3', '', title));
      const t = node('table', 'risk-scoring-table'),
        head = node('tr');
      head.append(node('th', '', '기준'), node('th', '', '점수·가중치'));
      t.append(head);
      Object.entries(values).forEach(([key, value]) => {
        const row = node('tr');
        row.append(node('td', '', labelMap[key] || key), node('td', '', text(value)));
        t.append(row);
      });
      area.append(t);
    }
    table('현재 위협 등급별 점수', method.severity_scale, grades);
    table('미래 가능성 등급별 검토점수', method.future_attention_scale, grades);
    table('영향 분야 가중치', method.domain_weights, domains);
    area.append(
      node('p', '', method.domain_formula || ''),
      node('p', '', method.unknown_policy || '')
    );
    (method.limitations || []).forEach((v) => area.append(node('p', 'subtle', text(v))));
  }

  async function load() {
    route.write({ page, pending_page: reviewPage });
    const id = ++requestId;
    controller?.abort();
    controller = new AbortController();
    try {
      const data = await api('/api/risks?' + query(), controller.signal);
      if (id === requestId) {
        $('#risk-notice').hidden = true;
        render(data);
      }
    } catch (e) {
      if (e.name === 'AbortError') return;
      $('#risk-notice').hidden = false;
      $('#risk-notice').textContent = e.message;
    }
  }
  function section(parent, label, value) {
    if (value == null || (Array.isArray(value) && !value.length)) return;
    parent.append(node('h3', '', label));
    (Array.isArray(value) ? value : [value]).forEach((v) => parent.append(node('p', '', text(v))));
  }
  async function appendSimilarRisks(area, r) {
    const block = node('div', 'risk-similar-block');
    block.append(
      node('h3', '', '유사 위험·공통 검토 근거'),
      node(
        'p',
        'subtle',
        '같은 검토 근거를 공유하는 항목입니다. 검색 유사성·근거 연결을 보여줄 뿐, 인과관계나 발생 확률을 의미하지 않습니다.'
      )
    );
    try {
      const graph = await riskGraph(),
        nodes = graph.nodes || [],
        edges = graph.edges || [];
      const current = nodes.find(
        (n) => n.kind === 'risk' && (n.id === r.id || n.label === r.title || n.name === r.title)
      );
      if (!current) {
        block.append(node('p', 'empty', '이 위험과 연결된 GraphRAG 유사 위험을 찾지 못했습니다.'));
        area.append(block);
        return;
      }
      const related = [];
      edges
        .filter(
          (e) =>
            e.relation === '공유 검토 근거 · 유사성' &&
            (e.source === current.id || e.target === current.id)
        )
        .forEach((e) => {
          const id = e.source === current.id ? e.target : e.source,
            n = nodes.find((item) => item.id === id && item.kind === 'risk');
          if (n) related.push({ node: n, edge: e });
        });
      related.sort(
        (a, b) =>
          (b.edge.similarity || 0) - (a.edge.similarity || 0) ||
          (b.node.weight || 0) - (a.node.weight || 0)
      );
      if (!related.length) {
        block.append(node('p', 'empty', '공유 검토 근거가 있는 유사 위험이 없습니다.'));
        area.append(block);
        return;
      }
      const list = node('ul', 'risk-similar-list');
      related.slice(0, 12).forEach(({ node: n, edge }) => {
        const item = node('li', 'risk-similar-item'),
          title = node('strong', '', n.label || n.name);
        item.append(
          title,
          node(
            'span',
            'subtle',
            `유사성 ${edge.similarity ?? '미상'} · 가중치 ${n.weight ?? '미상'} · 공유 근거 ${n.shared_evidence_count ?? 0}개`
          )
        );
        const open = node('button', 'text-link', '위험 상세 열기');
        open.type = 'button';
        open.addEventListener('click', () => detail(n.id));
        item.append(open);
        list.append(item);
      });
      block.append(list);
      if (related.length > 12)
        block.append(
          node('p', 'subtle', `유사 위험 ${num(related.length)}개 중 상위 12개를 표시합니다.`)
        );
    } catch (error) {
      block.append(
        node(
          'p',
          'empty',
          '유사 위험을 불러오지 못했습니다. 관계 지도에서 다시 확인할 수 있습니다.'
        )
      );
    }
    area.append(block);
  }
  async function detail(id) {
    if (!restoring) route.write({ risk: id }, 'push');
    const dialog = $('#risk-dialog'),
      area = $('#risk-detail');
    area.replaceChildren(node('p', '', '위험 상세를 불러오는 중입니다.'));
    if (!dialog.open) dialog.showModal();
    try {
      const data = await api('/api/risks/' + encodeURIComponent(id)),
        r = data.risk;
      area.replaceChildren(node('h2', '', r.title), priorityBlock(r));
      if (r.resolution) {
        const v = r.resolution;
        section(area, '철회·정정 이력', [
          r.lifecycle === 'withdrawn_source_mismatch'
            ? '원문 연결 오류로 철회'
            : r.lifecycle === 'superseded'
              ? '후속 검증 평가로 대체된 과거 기록'
              : '문맥·출처 정정 검토',
          v.reason,
          v.verification_status === 'resolved'
            ? '후속 독립 검토와 현재 원문 일치 확인'
            : '후속 독립 검토 미완료',
        ]);
        if (v.corrected_source_url) area.append(link('교정된 원문', v.corrected_source_url));
        if (v.replacement_workflow_run_id)
          section(area, '후속 재평가 ID', v.replacement_workflow_run_id);
      }
      section(area, '검토 우선순위의 산정 근거', r.priority?.explanation);
      section(
        area,
        '현재 위협과 향후 시나리오',
        `현재 ${grades[r.current_severity] || '미상'} · 향후 ${grades[r.future_likelihood] || '미상'} / ${horizons[r.horizon] || '기간 미상'}`
      );
      if (r.assessed_current_severity && r.current_severity !== r.assessed_current_severity)
        section(
          area,
          '과거 근거 당시 평가',
          `${grades[r.assessed_current_severity]} · 원문 ${r.evidence_latest_date || '날짜 미상'}. 현재 등급과 구분합니다.`
        );
      [
        ['현재 판단 근거', r.current_basis],
        ['향후 시나리오', r.scenario],
        ['불확실성', r.uncertainty],
        ['관측 신호', r.observed_indicators],
        ['위험 상승 조건', r.escalation_signals],
        ['완화 조건·대응', r.mitigations],
        ['반대 근거', r.counter_evidence],
        ['가정', r.assumptions],
      ].forEach(([label, value]) => section(area, label, value));
      section(area, '현재 원문 일치 상태', r.source_status);
      if (r.source_checks?.length) {
        area.append(node('h3', '', '원문 변경·최신성 확인'));
        r.source_checks.forEach((check) => {
          const box = node('div', 'risk-evidence-entry');
          box.append(
            node('p', '', check.reason || check.status || '확인 상태 미상'),
            node(
              'p',
              'subtle',
              `현재 원문 일치: ${check.current_source_match == null ? '미확인' : check.current_source_match ? '일치' : '불일치'} · 기준 날짜 ${(check.source_dates || []).join(', ') || '미상'}`
            )
          );
          if (check.date_note || check.date_basis)
            box.append(node('p', 'subtle', check.date_note || check.date_basis));
          area.append(box);
        });
      }
      area.append(node('h3', '', '원문 근거'));
      (r.evidence || []).forEach((e) => {
        const card = node('div', 'risk-evidence-entry');
        card.append(
          link(e.title || '원문', e.source_url || e.url),
          node('p', 'subtle', `근거 날짜 ${e.day || e.days?.join(', ') || '미상'}`)
        );
        if (e.text || e.quote) card.append(node('p', '', e.text || e.quote));
        area.append(card);
      });
      await appendSimilarRisks(area, r);
    } catch (e) {
      area.replaceChildren(node('p', '', e.message));
    }
  }
  $('#risk-dialog').addEventListener('close', () => {
    if (!restoring) route.write({ risk: '' });
  });
  addEventListener('popstate', () => {
    restoring = true;
    const saved = restoreRoute();
    load();
    if (saved.risk) detail(saved.risk);
    else $('#risk-dialog').close();
    restoring = false;
  });
  $('#risk-close').addEventListener('click', () => $('#risk-dialog').close());
  $('#risk-refresh').addEventListener('click', load);
  ['risk-sort', 'risk-domain', 'risk-severity', 'risk-likelihood', 'risk-status'].forEach((id) =>
    $('#' + id).addEventListener('change', () => {
      page = 1;
      reviewPage = 1;
      route.write({ page, pending_page: reviewPage }, 'push');
      load();
    })
  );
  let searchTimer;
  $('#risk-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      page = 1;
      reviewPage = 1;
      load();
    }, 350);
  });
  $('#risk-clear').addEventListener('click', () => {
    ['risk-search', 'risk-domain', 'risk-severity', 'risk-likelihood'].forEach(
      (id) => ($('#' + id).value = '')
    );
    $('#risk-status').value = 'all';
    $('#risk-sort').value = 'priority';
    page = 1;
    reviewPage = 1;
    load();
  });
  $('#risk-prev').addEventListener('click', () => {
    page--;
    route.write({ page }, 'push');
    load();
  });
  $('#risk-next').addEventListener('click', () => {
    page++;
    route.write({ page }, 'push');
    load();
  });
  load();
  const requestedRisk = new URLSearchParams(location.search).get('risk');
  if (requestedRisk) detail(requestedRisk);
})();
