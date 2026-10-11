(() => {
  'use strict';
  const $ = (s) => document.querySelector(s),
    node = (tag, text, cls) => {
      const e = document.createElement(tag);
      e.textContent = text || '';
      if (cls) e.className = cls;
      return e;
    };
  let timer = null,
    active = '',
    busy = false,
    epoch = 0;
  async function api(url, body) {
    const r = await fetch(
      url,
      body
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          }
        : {}
    );
    const d = await r.json();
    if (!r.ok) throw Error(d.error || '전망 조회 실패');
    return d;
  }
  function render(job) {
    const root = $('#forecast-results');
    root.replaceChildren();
    $('#forecast-status').textContent =
      `${job.title || '조건부 전망'} · ${{ complete: '독립 검토 통과', running: '전망 생성·독립 검토 중', queued: '실행 대기', needs_review: '보완 필요', failed: '실행 실패', interrupted: '중단됨' }[job.status] || job.status}${job.error ? ' · ' + job.error : ''}`;
    if (job.status !== 'complete' || job.result?.verified !== true) {
      if (job.status === 'needs_review')
        root.append(node('p', '검토를 통과하지 못한 수치와 예측은 표시하지 않습니다.'));
      return;
    }
    const result = job.result;
    root.append(
      node(
        'p',
        `전망 기준일 ${result.as_of} · 이후 ${result.horizon_days}일 범위 · ${result.basis}`
      ),
      node('p', result.report.summary)
    );
    for (const item of result.report.comparisons) {
      const card = node('article', null, 'card forecast-card');
      card.append(node('h3', item.title), node('p', item.assessment));
      const numeric = item.method === 'subjective' && typeof item.probability_percent === 'number';
      const metrics = node('div', null, 'forecast-metrics');
      metrics.append(
        node(
          'div',
          `예상 시기\n${item.window_start && item.window_end ? item.window_start + ' ~ ' + item.window_end : '산정 불가'}`
        ),
        node(
          'div',
          `발생 확률\n${numeric ? item.probability_percent + '% · 주관적 추정' : '산정 불가'}`
        ),
        node(
          'div',
          `확률의 불확실성 범위\n${numeric ? item.probability_lower + '~' + item.probability_upper + '% · 폭 ' + (item.probability_upper - item.probability_lower) + '%p' : '수치화 근거 부족'}`
        )
      );
      card.append(metrics);
      if (numeric) {
        const bar = node('div', null, 'probability-bar');
        bar.setAttribute('role', 'img');
        bar.setAttribute(
          'aria-label',
          `주관적 확률 ${item.probability_percent}%, 범위 ${item.probability_lower}~${item.probability_upper}%`
        );
        const range = node('span', null, 'probability-range');
        range.style.left = item.probability_lower + '%';
        range.style.width = item.probability_upper - item.probability_lower + '%';
        const dot = node('span', null, 'probability-point');
        dot.style.left = item.probability_percent + '%';
        bar.append(range, dot);
        card.append(bar);
      }
      for (const [label, value] of [
        ['추정 근거', item.probability_basis],
        ['사건 판정 기준', item.resolution_criterion],
        ['성립 조건', item.assumptions.join(' / ')],
        ['반대 근거·반증 조건', item.counter_evidence.join(' / ')],
        ['다시 평가할 신호', item.update_signals.join(' / ')],
      ])
        card.append(node('h4', label), node('p', value));
      const sources = node('details');
      sources.append(node('summary', '인용 근거 확인'));
      for (const e of result.evidence || []) {
        if (!item.evidence_ids.includes(e.id)) continue;
        const p = node('p', `${e.published_day || '날짜 미상'} · `),
          a = node('a', e.title);
        try {
          const u = new URL(e.source_url || e.url);
          if (['https:', 'http:'].includes(u.protocol)) {
            a.href = u.href;
            a.rel = 'noopener noreferrer';
          }
        } catch (_) {}
        p.append(a);
        sources.append(p);
      }
      card.append(sources);
      root.append(card);
    }
    for (const text of result.report.limitations) root.append(node('p', text, 'legend'));
    // The stored title is the submitted forecast question, even after editing
    // the input or opening a different saved forecast.
    ObservatorySearch.highlightQuestion(root, job.title || '');
  }
  async function poll(id) {
    try {
      const d = await api('/api/intelligence?view=job&id=' + encodeURIComponent(id));
      if (id !== active) return;
      if (!d.item) throw Error('저장된 전망이 없습니다.');
      render(d.item);
      if (['running', 'queued'].includes(d.item.status)) timer = setTimeout(() => poll(id), 4000);
      else {
        busy = false;
        $('#forecast-start').disabled = false;
      }
    } catch (e) {
      if (id !== active) return;
      $('#forecast-status').textContent = e.message;
      busy = false;
      $('#forecast-start').disabled = false;
    }
  }
  $('#forecast-start').onclick = async () => {
    const question = $('#query').value.trim();
    if (question.length < 3) {
      $('#forecast-status').textContent = '위 질의창에 3자 이상의 주제나 질문을 입력하세요.';
      return;
    }
    if (busy) return;
    clearTimeout(timer);
    busy = true;
    const version = epoch;
    $('#forecast-start').disabled = true;
    $('#forecast-results').replaceChildren();
    $('#forecast-status').textContent = '현재 검토된 근거를 모아 전망을 요청하고 있습니다…';
    try {
      const d = await api('/api/intelligence/forecasts', {
        question,
        horizon_days: Number($('#forecast-horizon').value),
        mode: 'hybrid',
      });
      if (version !== epoch) return;
      active = d.run.id;
      const p = new URLSearchParams(location.search);
      p.set('forecast', active);
      history.replaceState(null, '', '/context?' + p);
      poll(active);
    } catch (e) {
      if (version !== epoch) return;
      $('#forecast-status').textContent = e.message;
      busy = false;
      $('#forecast-start').disabled = false;
    }
  };
  $('#forecast-history').onclick = async () => {
    try {
      const d = await api('/api/intelligence?view=forecasts');
      const root = $('#forecast-results');
      root.replaceChildren();
      $('#forecast-status').textContent =
        '저장된 전망 · 선택 시 현재 근거 유효성을 다시 확인합니다.';
      for (const item of d.items) {
        const b = node(
          'button',
          `${item.created_at.slice(0, 10)} · ${item.title} · ${item.status}`
        );
        b.onclick = () => {
          clearTimeout(timer);
          active = item.id;
          poll(active);
        };
        root.append(b);
      }
      if (!d.items.length) root.append(node('p', '저장된 전망이 없습니다.'));
    } catch (e) {
      $('#forecast-status').textContent = e.message;
    }
  };
  document.addEventListener('context:query', () => {
    epoch++;
    clearTimeout(timer);
    active = '';
    busy = false;
    $('#forecast-start').disabled = false;
    $('#forecast-results').replaceChildren();
    $('#forecast-status').textContent = '같은 질의로 조건부 전망을 생성할 수 있습니다.';
  });
  const initial = new URLSearchParams(location.search).get('forecast') || '';
  if (/^analysis:[a-f0-9]{32}$/.test(initial)) {
    active = initial;
    poll(active);
  }
  addEventListener('pagehide', () => clearTimeout(timer));
})();
