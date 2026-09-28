/* Improvement panel; initialized by the workbench composition root. */
(() => {
  'use strict';
  window.StrategyPanels ||= {};
  window.StrategyPanels.improvement = (context) => {
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
    let activeImprovement = null,
      improvementTimer;
    const improvementStates = {
      running: '분석 중',
      waiting: '새 근거 대기',
      finishing: '현재 회차 마무리 중',
      paused: '일시중지',
      complete: '완료',
      budget_exhausted: '설정 회차 종료',
      stopped: '진행 변화 없어 종료',
      error: '확인 필요',
      planned: '회차 준비',
      needs_review: '검토 필요',
      failed: '실패',
    };
    function renderImprovement(run) {
      activeImprovement = run;
      clearTimeout(improvementTimer);
      $('#improvement-state').textContent = improvementStates[run.status] || run.status;
      $('#improvement-state').className =
        'pill ' +
        (run.status === 'complete'
          ? 'green'
          : ['error', 'stopped'].includes(run.status)
            ? 'amber'
            : '');
      const busy = ['running', 'waiting', 'finishing'].includes(run.status);
      $('#improvement-start').disabled = busy;
      $('#improvement-pause').hidden = !busy;
      $('#improvement-pause').disabled = run.pause_requested || run.status === 'finishing';
      $('#improvement-resume').hidden = !['paused', 'error', 'stopped'].includes(run.status);
      let summary = improvementText(run.metrics);
      if (run.status === 'waiting')
        summary += ' · 새로운 근거를 기다립니다. 동일 근거로 AI 분석을 반복하지 않습니다.';
      if (run.pause_requested || run.status === 'finishing')
        summary += ' · 일시중지를 요청했습니다. 진행 중인 회차를 마무리한 뒤 멈춥니다.';
      if (run.next_run_at)
        summary += ' · 다음 확인 ' + new Date(run.next_run_at).toLocaleString('ko-KR');
      if (run.error) summary += ' · ' + run.error;
      if (run.history_scope) summary += ' · ' + run.history_scope;
      summary +=
        ' · 미처리 0건은 모든 뉴스의 검증 완료를 뜻하지 않습니다. 검토 필요와 분석 실패를 따로 확인하세요.';
      $('#improvement-summary').textContent = summary || '회차를 준비하고 있습니다.';
      const metrics = run.metrics || {},
        hasLedger = 'completion_total' in metrics;
      const comparisonFields = [
        ['total_unique', '전체 고유 뉴스'],
        ['processed_unique', '분석 처리'],
        ['verified_unique', '검토 통과'],
        ['needs_review_unique', '검토 필요'],
        ['remaining', '미처리 뉴스'],
        ['failed_unique', '분석 실패'],
        ['risk_assessed_unique', '위험 평가 뉴스'],
        ['risk_unassessed_unique', '위험 미평가 뉴스'],
        ['risk_reviewed_unique', '위험 독립 검토'],
      ];
      const primaryFields = hasLedger
        ? [
            ['completion_total', '전수 점검 대상'],
            ['completion_complete', '심층·위험 검토 완료'],
            ['completion_pending', '대기'],
            ['completion_running', '분석 중'],
            ['completion_needs_review', '보완 필요'],
            ['completion_failed', '실패'],
            ['active_workflows', '병렬 분석 작업'],
            ['risk_information_insufficient', '위험 검토 · 판단 근거 부족'],
          ]
        : [
            ...comparisonFields,
            ['active_workflows', '병렬 분석 작업'],
            ['risk_information_insufficient', '위험 검토 · 판단 근거 부족'],
          ];
      const metricCells = (fields) =>
        fields
          .filter(([key]) => key in metrics)
          .map(([key, label]) => {
            const cell = node('div');
            cell.append(node('span', '', label), node('strong', '', num(metrics[key])));
            return cell;
          });
      $('#improvement-progress').replaceChildren(...metricCells(primaryFields));
      const metricDetails = $('#improvement-metric-details');
      metricDetails.hidden = !hasLedger;
      $('#improvement-secondary-progress').replaceChildren(
        ...(hasLedger ? metricCells(comparisonFields) : [])
      );
      if (run.view === 'status') return;
      const timeline = $('#improvement-timeline');
      timeline.replaceChildren();
      (run.rounds || [])
        .slice()
        .reverse()
        .forEach((round) => {
          const card = node('article', 'improvement-round');
          card.append(
            node(
              'h4',
              '',
              `${round.number}회차 · ${improvementStates[round.status] || round.status}`
            )
          );
          const badges = node('div', 'improvement-meta');
          badges.append(
            node('span', 'pill', `신규 근거 ${num(round.new_document_count)}개`),
            node('span', 'pill', `원문 근거 ${num(round.source_count)}개`),
            node(
              'span',
              'pill',
              `새 제안 개념 ${num((round.catalog?.added || []).filter((c) => c.kind === 'strategic_concept').length)}개`
            )
          );
          card.append(badges);
          if (round.comparison) {
            card.append(
              node('p', '', improvementText(round.comparison.assessment)),
              node(
                'p',
                'subtle',
                {
                  baseline: '첫 회차 · 비교 기준 설정',
                  same_news_scope: '동일 뉴스 범위의 전후 비교',
                  different_news_scope:
                    '서로 다른 뉴스 범위 · 수치 변화가 품질 개선을 뜻하지 않습니다.',
                }[round.comparison.comparability] || improvementText(round.comparison.comparability)
              )
            );
            if (round.comparison.changes)
              card.append(node('p', '', improvementText(round.comparison.changes)));
          }
          if (round.before || round.after) {
            const detail = node('details');
            detail.append(
              node('summary', '', '회차 전후 지표'),
              node('p', '', '이전: ' + improvementText(round.before)),
              node('p', '', '이후: ' + improvementText(round.after))
            );
            card.append(detail);
          }
          const tasks = (run.tasks || []).filter((t) => t.source_run_id === round.workflow_run_id);
          if (tasks.length) {
            card.append(node('h4', '', '검토 지적과 후속 과제'));
            tasks.forEach((t) => card.append(node('p', '', `${t.text} · ${t.status}`)));
          }
          const rules = (run.rules || []).filter((t) => t.source_run_id === round.workflow_run_id);
          if (rules.length) {
            card.append(node('h4', '', '누적 개선 규칙'));
            rules.forEach((t) => card.append(node('p', '', t.text)));
          }
          if (round.error) card.append(node('p', 'subtle', round.error));
          if (round.workflow_run_id) {
            const inspect = node('button', 'button', '역할별 분석·검토 보기');
            inspect.addEventListener('click', () => {
              loadWorkflow(round.workflow_run_id, true);
              $('#workflow').scrollIntoView({ behavior: 'smooth' });
            });
            card.append(inspect);
          }
          timeline.append(card);
        });
      if (!timeline.childElementCount) empty(timeline, '새 근거를 확인하면 첫 회차가 시작됩니다.');
      renderImprovementCatalog(run);
    }
    function renderImprovementCatalog(run) {
      const el = $('#improvement-catalog');
      el.replaceChildren();
      const catalogs = run.catalog
        ? [run.catalog]
        : (run.rounds || []).map((r) => r.catalog).filter(Boolean);
      const byId = new Map();
      catalogs.forEach((c) =>
        (c.items || [...(c.added || []), ...(c.updated || [])]).forEach((item) =>
          byId.set(item.id, item)
        )
      );
      const kinds = {
        observed_keyword: '관측 용어',
        strategic_concept: '제안 개념',
        claim_relation: '검토된 해석',
      };
      const statuses = {
        observed_in_sources: '원문에서 관측',
        proposed: '제안 · 추가 검토 필요',
        reviewed_interpretation: '검토된 해석',
      };
      Array.from(byId.values())
        .slice(-40)
        .reverse()
        .forEach((item) => {
          const card = node('article', 'catalog-item');
          card.append(
            node('span', 'pill', kinds[item.kind] || '검토 기억'),
            node('span', 'pill', statuses[item.epistemic_status] || item.status || '검토 대기'),
            node('h4', '', item.label || item.name || '')
          );
          if (item.detail) card.append(node('p', '', item.detail));
          if (item.uncertainty || item.caveat)
            card.append(node('p', 'subtle', item.uncertainty || item.caveat));
          improvementEvidence(card, item.evidence || item.evidence_ids);
          el.append(card);
        });
      if (!el.childElementCount)
        empty(el, '회차가 끝나면 관측 용어와 제안 개념을 근거와 함께 표시합니다.');
    }
    async function loadImprovement(id, detail = false) {
      try {
        const data = await api(
          '/api/improvement' +
            (id ? '/' + encodeURIComponent(id) : '') +
            (detail ? '' : '?view=status')
        );
        const run = id ? data.run || data : data.runs?.[0];
        if (run) {
          if (detail || run.version !== activeImprovement?.version) renderImprovement(run);
          if (['running', 'waiting', 'finishing'].includes(run.status))
            scheduleStatus(
              'improvement',
              () => loadImprovement(run.id),
              run.status === 'waiting' ? 15000 : 5000
            );
          else stopStatus('improvement');
        }
      } catch (e) {
        $('#improvement-summary').textContent = e.message;
        retryStatus('improvement', () => loadImprovement(id), e);
      }
    }

    $('#improvement-start').addEventListener('click', () =>
      action($('#improvement-start'), async () => {
        const data = await api('/api/improvement?' + query(), {
          interval_seconds: Number($('#improvement-interval').value),
          max_rounds: Number($('#improvement-rounds').value),
          news_limit: Number($('#improvement-limit').value),
          full_corpus: $('#improvement-scope').value === 'all',
        });
        renderImprovement(data.run || data);
        loadImprovement((data.run || data).id);
      })
    );
    $('#improvement-scope').addEventListener('change', () => {
      $('#improvement-start').textContent =
        $('#improvement-scope').value === 'all'
          ? '전체 뉴스 분석·자기개선 시작'
          : '현재 범위 분석·자기개선 시작';
    });
    $('#improvement-refresh').addEventListener('click', () =>
      loadImprovement(activeImprovement?.id)
    );
    ['pause', 'resume'].forEach((actionName) =>
      $('#improvement-' + actionName).addEventListener('click', () =>
        action($('#improvement-' + actionName), async () => {
          if (!activeImprovement) return;
          const data = await api(
            '/api/improvement/' + encodeURIComponent(activeImprovement.id) + '/' + actionName,
            {}
          );
          renderImprovement(data.run || data);
          loadImprovement((data.run || data).id);
        })
      )
    );

    return {
      load: loadImprovement,
      details: () => activeImprovement && loadImprovement(activeImprovement.id, true),
    };
  };
})();
