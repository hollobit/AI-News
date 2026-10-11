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
    ticker = null,
    active = '',
    busy = false,
    epoch = 0,
    current = null,
    receivedAt = 0,
    preparingAt = 0,
    disconnected = false,
    failures = 0;
  const running = (job) => ['running', 'queued'].includes(job?.status);
  const duration = (seconds) => {
    const s = Math.max(0, Math.round(seconds));
    return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
  };
  async function api(url, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), body ? 90000 : 20000);
    try {
      const r = await fetch(url, {
        signal: controller.signal,
        ...(body
          ? {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            }
          : {}),
      });
      if (r.status === 401 || r.redirected) {
        throw Object.assign(Error('원격 로그인이 필요합니다.'), { unauthorized: true });
      }
      const d = await r.json();
      if (!r.ok) throw Object.assign(Error(d.error || '전망 조회 실패'), { status: r.status });
      return d;
    } finally {
      clearTimeout(timeout);
    }
  }
  function stopTracking() {
    epoch++;
    clearTimeout(timer);
    clearInterval(ticker);
    current = null;
    preparingAt = 0;
    disconnected = false;
    failures = 0;
    $('#forecast-resume').hidden = true;
    $('#forecast-login').hidden = true;
    $('#forecast-history').disabled = false;
  }
  function tick() {
    const panel = $('#forecast-metrics'),
      meter = $('#forecast-meter');
    if (preparingAt) {
      panel.hidden = false;
      meter.hidden = false;
      meter.removeAttribute('value');
      $('#forecast-progress').replaceChildren(node('li', '질의 관련 근거 준비'));
      $('#forecast-count').textContent = `요청 경과 ${duration((Date.now() - preparingAt) / 1000)}`;
      $('#forecast-eta').textContent = '근거 준비 후 실행 단계와 예상 남은 시간을 표시합니다.';
      return;
    }
    const data = current?.progress;
    panel.hidden = !data;
    if (!data) return;
    const delta = running(current) ? Math.max(0, (Date.now() - receivedAt) / 1000) : 0;
    meter.hidden = data.percent === null;
    if (data.percent !== null) meter.value = data.percent;
    $('#forecast-count').textContent =
      (data.recorded
        ? `완료 단계 ${data.completed}/${data.total} · ${data.percent}% · `
        : '이전 작업 · 단계별 기록 없음 · ') +
      `전체 경과 ${duration(data.elapsed_seconds + delta)}`;
    $('#forecast-progress').replaceChildren(
      ...data.stages.map((stage) => {
        const seconds =
          stage.seconds === null
            ? ''
            : ` · ${duration(stage.seconds + (stage.status === 'running' ? delta : 0))}`;
        const item = node('li', stage.label + seconds);
        item.dataset.done = String(stage.status === 'complete');
        item.dataset.stopped = String(stage.status === 'stopped');
        if (stage.status === 'running') item.setAttribute('aria-current', 'step');
        return item;
      })
    );
    let eta;
    if (!running(current)) {
      eta =
        current.status === 'complete'
          ? '독립 검토와 현재 근거 확인을 마쳤습니다.'
          : '처리가 종료되었습니다. 위 상태와 오류 내용을 확인하세요.';
    } else if (disconnected) {
      eta = `마지막 상태 확인 ${duration(delta)} 전 · 연결이 복구되면 진행도와 예상을 갱신합니다.`;
    } else if (current.status === 'queued') {
      eta = `앞선 작업 ${data.queue_ahead}건 · 실행이 시작되면 남은 시간을 갱신합니다.`;
      if (data.typical_seconds !== null)
        eta += ` 최근 전망 전체 소요 약 ${duration(data.typical_seconds)}.`;
    } else if (data.remaining_seconds === null) {
      eta = `남은 시간 산정 중 · 최근 완료 기록 ${data.estimate_samples}건, 최소 3건의 실측이 필요합니다.`;
    } else if (data.estimate_overdue || delta >= data.remaining_seconds) {
      eta = '실측 예상보다 오래 걸리고 있습니다. 현재 단계의 완료를 기다리고 있습니다.';
    } else {
      eta = `남은 예상 약 ${duration(data.remaining_seconds - delta)} · 최근 ${data.estimate_samples}건 ${data.estimate_basis === 'stages' ? '단계별' : '전체 소요'} 실측 기준의 참고값`;
    }
    $('#forecast-eta').textContent = eta;
  }
  function startTicker() {
    clearInterval(ticker);
    tick();
    ticker = setInterval(tick, 1000);
  }
  function remember(id) {
    const p = new URLSearchParams(location.search);
    if (id) p.set('forecast', id);
    else p.delete('forecast');
    history.replaceState(null, '', '/context?' + p);
  }
  function render(job) {
    current = job;
    receivedAt = Date.now();
    preparingAt = 0;
    $('#forecast-history').disabled = false;
    disconnected = false;
    busy = running(job);
    $('#forecast-start').disabled = busy;
    $('#forecast-resume').hidden = true;
    $('#forecast-login').hidden = true;
    clearInterval(ticker);
    if (busy) startTicker();
    else tick();
    const root = $('#forecast-results');
    root.replaceChildren();
    $('#forecast-status').textContent = `${job.title || '조건부 전망'} · ${
      job.status === 'running' && job.progress?.stages?.some((s) => s.status === 'running')
        ? job.progress.stages
            .filter((s) => s.status === 'running')
            .map((s) => s.label)
            .join(' · ') + ' 진행 중'
        : {
            complete: '독립 검토 통과',
            running: '전망 생성·독립 검토 중',
            queued: '실행 대기',
            needs_review: '보완 필요',
            failed: '실행 실패',
            interrupted: '중단됨',
          }[job.status] || job.status
    }${job.error ? ' · ' + job.error : ''}`;
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
  async function poll(id, version = epoch) {
    try {
      const d = await api('/api/intelligence?view=job&id=' + encodeURIComponent(id));
      if (id !== active || version !== epoch) return;
      if (!d.item) throw Error('저장된 전망이 없습니다.');
      failures = 0;
      render(d.item);
      if (running(d.item)) timer = setTimeout(() => poll(id, version), 3000);
    } catch (e) {
      if (id !== active || version !== epoch) return;
      disconnected = true;
      $('#forecast-resume').hidden = false;
      // An uncertain GET never authorizes a duplicate generation request.
      busy = true;
      $('#forecast-start').disabled = true;
      if (e.unauthorized) {
        $('#forecast-status').textContent = e.message;
        $('#forecast-login').hidden = false;
        $('#forecast-login').href = location.href;
      } else if (e.status === 404 || e.status === 400) {
        $('#forecast-status').textContent = e.message;
        clearInterval(ticker);
        current = null;
        busy = false;
        $('#forecast-start').disabled = false;
      } else {
        failures++;
        $('#forecast-status').textContent =
          '상태 연결이 끊겼습니다. 같은 전망의 진행 상태를 자동으로 다시 확인합니다.';
        timer = setTimeout(() => poll(id, version), Math.min(15000, 3000 * failures));
      }
      tick();
    }
  }
  $('#forecast-start').onclick = async () => {
    const question = $('#query').value.trim();
    if (question.length < 3) {
      $('#forecast-status').textContent = '위 질의창에 3자 이상의 주제나 질문을 입력하세요.';
      return;
    }
    if (busy) return;
    stopTracking();
    active = '';
    busy = true;
    const version = epoch;
    $('#forecast-start').disabled = true;
    $('#forecast-results').replaceChildren();
    $('#forecast-status').textContent = '현재 검토된 근거를 모아 전망을 요청하고 있습니다…';
    preparingAt = Date.now();
    $('#forecast-history').disabled = true;
    startTicker();
    try {
      const d = await api('/api/intelligence/forecasts', {
        question,
        horizon_days: Number($('#forecast-horizon').value),
        mode: 'hybrid',
      });
      if (version !== epoch) return;
      active = d.run.id;
      remember(active);
      render(d.run);
      if (running(d.run)) timer = setTimeout(() => poll(active, version), 1000);
    } catch (e) {
      if (version !== epoch) return;
      clearInterval(ticker);
      preparingAt = 0;
      $('#forecast-history').disabled = false;
      $('#forecast-metrics').hidden = true;
      $('#forecast-status').textContent =
        e.message + ' · 요청 결과가 불명확하면 저장된 전망 보기에서 먼저 확인하세요.';
      busy = false;
      $('#forecast-start').disabled = false;
    }
  };
  $('#forecast-history').onclick = async () => {
    // Keep the accepted request attached until its job ID is received.
    if (preparingAt) return;
    stopTracking();
    active = '';
    busy = false;
    $('#forecast-start').disabled = false;
    $('#forecast-metrics').hidden = true;
    remember('');
    const version = epoch;
    try {
      const d = await api('/api/intelligence?view=forecasts');
      if (version !== epoch) return;
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
          stopTracking();
          active = item.id;
          busy = true;
          $('#forecast-start').disabled = true;
          remember(active);
          poll(active);
        };
        root.append(b);
      }
      if (!d.items.length) root.append(node('p', '저장된 전망이 없습니다.'));
    } catch (e) {
      if (version !== epoch) return;
      $('#forecast-status').textContent = e.message;
    }
  };
  document.addEventListener('context:query', () => {
    stopTracking();
    active = '';
    busy = false;
    $('#forecast-start').disabled = false;
    $('#forecast-results').replaceChildren();
    $('#forecast-metrics').hidden = true;
    $('#forecast-status').textContent = '같은 질의로 조건부 전망을 생성할 수 있습니다.';
  });
  const initial = new URLSearchParams(location.search).get('forecast') || '';
  if (/^analysis:[a-f0-9]{32}$/.test(initial)) {
    active = initial;
    busy = true;
    $('#forecast-start').disabled = true;
    poll(active);
  }
  $('#forecast-resume').onclick = () => {
    clearTimeout(timer);
    epoch++;
    poll(active);
  };
  addEventListener('pagehide', () => {
    epoch++;
    clearTimeout(timer);
    clearInterval(ticker);
  });
  addEventListener('pageshow', (event) => {
    if (event.persisted && active) poll(active);
  });
})();
