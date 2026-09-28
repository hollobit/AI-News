/* Risks panel; initialized by the workbench composition root. */
(() => {
  'use strict';
  window.StrategyPanels ||= {};
  window.StrategyPanels.risks = (context) => {
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
    let riskData = null;
    const riskGrades = {
      unknown: '미상',
      low: '낮음',
      moderate: '보통',
      high: '높음',
      critical: '심각',
    };
    const riskHorizons = {
      '0-3mo': '향후 0–3개월',
      '3-12mo': '향후 3–12개월',
      '12-36mo': '향후 1–3년',
    };
    const riskDomains = {
      economy: '국가경제',
      security: '국가안보',
      industry: '산업',
      exports: '수출',
      social: '사회적 문제',
      life: '생활',
      education: '교육',
    };
    function riskDetail(parent, title, value) {
      if (value == null || (Array.isArray(value) && !value.length)) return;
      const detail = node('details');
      detail.append(node('summary', '', title));
      (Array.isArray(value) ? value : [value]).forEach((v) =>
        detail.append(node('p', '', improvementText(v)))
      );
      parent.append(detail);
    }
    function renderRisks(data) {
      riskData = data;
      const coverage = data.coverage || {};
      $('#risk-counts').replaceChildren(
        ...[
          ['assessed', '근거 검토 완료 분석'],
          ['needs_review', '검토 필요 분석'],
          ['unassessed', '위험 미평가 분석'],
          ['source_review_required', '현재 원문 재검토 항목'],
        ].map(([key, label]) => {
          const card = node('div');
          const value =
            key === 'needs_review'
              ? (coverage.needs_review_workflows ?? coverage[key])
              : coverage[key];
          card.append(
            node('span', '', label),
            node('strong', '', value == null ? '집계 미제공' : num(value))
          );
          return card;
        })
      );
      $('#risk-summary').textContent =
        '검토 우선순위는 규칙 기반이며 발생 확률이 아닙니다. 미평가는 안전하거나 위험이 없다는 뜻이 아닙니다.';
      $('#risk-cards').replaceChildren(
        ...(data.items || []).slice(0, 3).map((r) => {
          const card = node('article', 'risk-summary-card'),
            p = r.priority || {};
          const score =
            p.status === 'partial' && p.range
              ? p.range.join('–') + '점 · 부분 평가'
              : p.status === 'needs_review'
                ? p.range
                  ? p.range.join('–') + '점 · 재검토 필요'
                  : '재검토 필요'
                : p.score == null
                  ? '미평가'
                  : p.score + ' / 100';
          card.append(
            node('span', 'pill', '검토 우선순위 ' + score),
            node('h3', '', r.title),
            node(
              'p',
              'subtle',
              `현재 ${riskGrades[r.current_severity] || '미상'} · 미래 ${riskGrades[r.future_likelihood] || '미상'}`
            ),
            link('평가 상세 보기 ↗', '/risks?risk=' + encodeURIComponent(r.id))
          );
          return card;
        })
      );
      if (!$('#risk-cards').childElementCount)
        empty(
          $('#risk-cards'),
          '표시할 평가가 없습니다. 전체 위험 평가에서 미평가·검토 필요 상태를 확인하세요.'
        );
    }
    async function loadRisks() {
      try {
        renderRisks(await api('/api/risks?view=page&page=1&page_size=3&sort=priority&status=all'));
      } catch (e) {
        $('#risk-summary').textContent = '위험 평가를 불러오지 못했습니다. ' + e.message;
      }
    }
    $('#risks-refresh').addEventListener('click', loadRisks);
    $('#risk-question').addEventListener('click', () => {
      $('#question').value =
        '현재 수집 근거에서 AI 안전·사이버·국가안보·산업의 현재 위협과 향후 위험을 구분하고, 위험 상승 신호·완화 조건·반대 근거·불확실성을 설명해 주세요.';
      state.selectedNodes = [];
      $('#network').scrollIntoView({ behavior: 'smooth' });
      $('#question').focus();
    });

    return { load: loadRisks };
  };
})();
