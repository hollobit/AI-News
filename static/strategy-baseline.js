/* Baseline panel; initialized by the workbench composition root. */
(() => {
  'use strict';
  window.StrategyPanels ||= {};
  window.StrategyPanels.baseline = (context) => {
    const {
      $,
      node,
      num,
      safe,
      link,
      notify,
      api,
      action,
      query,
      state,
      scheduleStatus,
      stopStatus,
      retryStatus,
      load,
      loadOverview,
      selectLens,
      selectedScope,
      empty,
      spark,
      svgEl,
      openStory,
      growth,
      loadWorkflow,
      improvementText,
      improvementEvidence,
      reading,
      renderMonitoring,
      renderTrends,
      renderNews,
      visibleLenses,
    } = context;
    let activeBaseline = null,
      baselineTimer;
    const baselineStates = {
      preparing: '전체 뉴스 준비 중',
      requires_review: '검토 필요 항목 확인',
      queued: '대기',
      running: '기본 분석 중',
      finishing: '진행 작업 마무리 중',
      paused: '일시중지',
      complete: '기본 분석 처리 종료',
      failed: '오류 확인 필요',
      error: '오류 확인 필요',
      waiting: '새 뉴스 대기',
    };
    function baselineDetail(analysis) {
      const box = node('section', 'baseline-detail'),
        value = analysis.result || analysis;
      box.append(
        node('h3', '', '전체 뉴스 기본 분석'),
        node(
          'span',
          'pill',
          analysis.verified ? '기본 분석 검토 통과' : '기본 분석 · 검토 상태 확인 필요'
        )
      );
      if (value.summary) box.append(node('p', '', value.summary));
      [
        ['keywords', '키워드'],
        ['sectors', '기술 분야'],
        ['strategic_relevance', '전략 관련성'],
        ['risk_signal', '기본 위험 신호'],
        ['limitations', '분석 한계'],
        ['strategic', '전략 단서'],
        ['strategic_signals', '전략 단서'],
        ['risk_flags', '기본 위험 신호'],
      ].forEach(([key, label]) => {
        if (
          value[key] &&
          (typeof value[key] !== 'object' || Array.isArray(value[key])
            ? value[key].length
            : Object.keys(value[key]).length)
        )
          box.append(
            node(
              'p',
              '',
              label +
                ': ' +
                (Array.isArray(value[key])
                  ? value[key]
                      .map((v) =>
                        typeof v === 'string'
                          ? v
                          : v.label || v.text || v.name || improvementText(v)
                      )
                      .join(' · ')
                  : improvementText(value[key]))
            )
          );
      });
      box.append(
        node(
          'p',
          'subtle',
          '기본 위험 신호는 후속 검토 대상이며 정밀 위험 등급·발생 확률을 뜻하지 않습니다.'
        )
      );
      if (value.keywords?.some((k) => k.source_quote)) {
        const sources = node('details');
        sources.append(node('summary', '', '키워드의 원문 표현'));
        value.keywords
          .filter((k) => k.source_quote)
          .forEach((k) => sources.append(node('p', '', k.label + ' · ' + k.source_quote)));
        box.append(sources);
      }
      if (analysis.source_scope)
        box.append(node('p', 'subtle', '기본 분석 근거 범위: ' + analysis.source_scope));
      improvementEvidence(box, value.evidence || analysis.evidence || analysis.evidence_ids);
      if (analysis.verification?.issues?.length)
        box.append(node('p', 'subtle', improvementText(analysis.verification.issues)));
      return box;
    }
    let baselineObserved = false,
      baselineFingerprint = '';
    function updateBaselineVisibility(run, metrics) {
      const fields = {
        id: run.id,
        status: run.status,
        error: run.error || '',
        metrics: Object.fromEntries(
          Object.keys(metrics)
            .sort()
            .filter((k) => !/(timestamp|updated_at|created_at)/.test(k))
            .map((k) => [k, metrics[k]])
        ),
      };
      const fingerprint = JSON.stringify(fields),
        details = $('#baseline-content');
      if (!baselineObserved) {
        let saved = '';
        try {
          saved = localStorage.getItem('news.baseline.lastContent') || '';
        } catch (_) {}
        details.open = saved !== fingerprint;
        baselineObserved = true;
      } else if (fingerprint !== baselineFingerprint) details.open = true;
      baselineFingerprint = fingerprint;
      try {
        localStorage.setItem('news.baseline.lastContent', fingerprint);
      } catch (_) {}
      details.querySelector('summary').textContent = details.open
        ? '기본 데이터 내용 · 접기'
        : '기본 데이터 내용 · 새 변경 없음 / 펼치기';
    }
    $('#baseline-content').addEventListener('toggle', () => {
      const d = $('#baseline-content');
      d.querySelector('summary').textContent = d.open
        ? '기본 데이터 내용 · 접기'
        : '기본 데이터 내용 · 펼치기';
    });
    function renderBaseline(run) {
      activeBaseline = run;
      clearTimeout(baselineTimer);
      const status = run.status;
      $('#baseline-state').textContent = baselineStates[status] || status;
      const busy = ['preparing', 'queued', 'running', 'finishing', 'waiting'].includes(status);
      $('#baseline-start').disabled = busy;
      $('#baseline-pause').hidden = !busy;
      $('#baseline-pause').disabled = Boolean(run.pause_requested) || status === 'finishing';
      $('#baseline-resume').hidden = !['paused', 'failed', 'error', 'requires_review'].includes(
        status
      );
      const metrics = run.metrics || run.progress || {};
      updateBaselineVisibility(run, metrics);
      const fields = [
        ['total', '이 분석의 고정 대상'],
        ['preprocessed', '전처리'],
        ['analyzed', 'AI 분석'],
        ['verified', '검토 통과'],
        ['pending', '분석 대기'],
        ['failed', '실패'],
        ['needs_review', '검토 필요'],
        ['active_workers', '진행 중인 작업자'],
      ];
      $('#baseline-progress').replaceChildren(
        ...fields.map(([key, label]) => {
          const box = node('div');
          box.append(
            node('span', '', label),
            node('strong', '', metrics[key] == null ? '집계 중' : num(metrics[key]))
          );
          return box;
        })
      );
      $('#baseline-summary').textContent =
        (run.error || run.message || '전체 뉴스의 기본 데이터를 누적하고 있습니다.') +
        ' 전처리 수는 AI 분석 완료 수와 다릅니다. 분석된 뉴스도 검토 통과·검토 필요·실패를 나누어 확인하세요.';
    }
    async function loadBaseline(id) {
      try {
        const data = await api(
          '/api/baseline' + (id ? '/' + encodeURIComponent(id) : '') + '?view=status'
        );
        const run = id ? data.run || data : data.runs?.[0];
        if (run) {
          if (run.version !== activeBaseline?.version) renderBaseline(run);
          if (['preparing', 'running', 'finishing', 'queued', 'waiting'].includes(run.status))
            scheduleStatus('baseline', () => loadBaseline(run.id));
          else stopStatus('baseline');
        } else {
          $('#baseline-state').textContent = '시작 전';
          if (data.enabled === false) $('#baseline-start').disabled = true;
        }
      } catch (e) {
        $('#baseline-summary').textContent = e.message;
        retryStatus('baseline', () => loadBaseline(id), e);
      }
    }

    $('#baseline-start').addEventListener('click', () =>
      action($('#baseline-start'), async () => {
        const data = await api('/api/baseline', { batch_size: 12, workers: 6 });
        renderBaseline(data.run || data);
        loadBaseline((data.run || data).id);
      })
    );
    $('#baseline-refresh').addEventListener('click', () => loadBaseline(activeBaseline?.id));
    ['pause', 'resume'].forEach((name) =>
      $('#baseline-' + name).addEventListener('click', () =>
        action($('#baseline-' + name), async () => {
          if (!activeBaseline) return;
          const data = await api(
            '/api/baseline/' + encodeURIComponent(activeBaseline.id) + '/' + name,
            {}
          );
          renderBaseline(data.run || data);
          loadBaseline((data.run || data).id);
        })
      )
    );

    return { load: loadBaseline, detail: baselineDetail };
  };
})();
