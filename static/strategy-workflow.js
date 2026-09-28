/* Workflow panel; initialized by the workbench composition root. */
(() => {
  'use strict';
  window.StrategyPanels ||= {};
  window.StrategyPanels.workflow = (context) => {
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

      improvementText,
      improvementEvidence,
      reading,
      renderMonitoring,
      renderTrends,
      renderNews,
      visibleLenses,
    } = context;
    const workflowLabels = {
      collection: '수집·스냅샷',
      enrichment: '원문 보강',
      source_repair: '원문 보완 조회',
      repair_morphology: '보완 원문 형태소 분석',
      morphology: '형태소 분석',
      graph_retrieval: 'GraphRAG 근거 검색',
      national: '국가·정책 분석',
      technology: '기술·사업 분석',
      synthesis: '전략 종합',
      verification: '근거 대조',
      risk_assessment: '위험 평가',
      risk_verification: '위험 근거 검증',
      risk_revision: '위험 평가 보완',
      risk_reverification: '위험 재검증',
      revision: '보완',
      reverification: '재검토',
      final: '결과 저장',
    };
    const workflowStatus = {
      queued: '대기',
      running: '진행 중',
      complete: '완료',
      failed: '실패',
      paused: '일시 중지',
      needs_review: '검토 필요',
      skipped: '생략',
    };
    let activeWorkflow = null,
      workflowTimer;
    function renderWorkflow(run) {
      activeWorkflow = run;
      $('#workflow-state').textContent = workflowStatus[run.status] || run.status;
      $('#workflow-state').className =
        'pill ' +
        (run.status === 'complete'
          ? 'green'
          : run.status === 'needs_review' || run.status === 'failed'
            ? 'amber'
            : '');
      const stages = run.stages || {};
      const groups = [
        ['collection', 'enrichment'],
        ['morphology', 'graph_retrieval'],
        ['national', 'technology'],
        ['verification', 'risk_assessment', 'risk_verification'],
        [
          'risk_revision',
          'risk_reverification',
          'source_repair',
          'repair_morphology',
          'revision',
          'reverification',
          'synthesis',
          'final',
        ],
      ];
      document.querySelectorAll('.agent-step').forEach((el, i) => {
        const values = groups[i].map((k) => stages[k]).filter(Boolean);
        el.classList.remove('running', 'complete', 'failed');
        if (values.includes('running')) el.classList.add('running');
        else if (values.includes('failed')) el.classList.add('failed');
        else if (values.length && values.every((v) => v === 'complete' || v === 'skipped'))
          el.classList.add('complete');
      });
      $('#workflow-events').replaceChildren(
        ...(run.events || [])
          .slice(-10)
          .reverse()
          .map((e) => {
            const row = node('div', 'workflow-event');
            row.append(
              node('b', '', workflowLabels[e.stage] || e.stage),
              document.createTextNode(
                (workflowStatus[e.status] || e.status) +
                  ' · ' +
                  (e.created_at || '').slice(11, 19) +
                  ' UTC'
              )
            );
            if (e.detail) row.append(node('div', 'subtle', e.detail));
            return row;
          })
      );
      const area = $('#workflow-results');
      area.replaceChildren();
      const result = run.results;
      if (result?.report) {
        area.append(node('p', 'workflow-result', result.report.summary));
        if (!result.verified)
          area.append(node('p', 'pill amber', '검토가 끝나지 않은 해석 · 의사결정 전 확인 필요'));
        (result.report.claims || []).slice(0, 8).forEach((c) => {
          const section = node('details', 'workflow-result');
          section.append(node('summary', '', c.title), node('p', '', c.detail));
          if (c.uncertainty) section.append(node('p', 'subtle', c.uncertainty));
          const evidence = (result.evidence || []).filter((e) =>
            (c.evidence_ids || []).includes(e.id)
          );
          evidence
            .slice(0, 3)
            .forEach((e) =>
              section.append(link(e.title || e.url || '메시지 근거', e.url || '/news?date=all'))
            );
          area.append(section);
        });
        (result.verification?.issues || []).forEach((t) => area.append(node('p', 'subtle', t)));
      } else {
        const artifacts = run.artifacts || {};
        for (const [role, value] of Object.entries(artifacts)) {
          if (!['national', 'technology', 'verification', 'graph_retrieval'].includes(role))
            continue;
          const section = node('details', 'workflow-result');
          section.append(
            node('summary', '', workflowLabels[role] || role),
            node(
              'p',
              'subtle',
              value.summary || value.report?.summary || '중간 산출물 저장됨 · 최종 검토 전'
            )
          );
          area.append(section);
        }
        if (!area.childElementCount)
          area.textContent =
            run.error ||
            `${run.snapshot_count}개 뉴스 근거로 분석합니다. 역할별 산출물을 기다리고 있습니다.`;
      }
      $('#workflow-resume').hidden = !['paused', 'failed'].includes(run.status);
      $('#workflow-start').disabled = ['queued', 'running'].includes(run.status);
    }
    function renderWorkflowStatus(run) {
      activeWorkflow = run;
      $('#workflow-state').textContent = workflowStatus[run.status] || run.status;
      const groups = [
        ['collection', 'enrichment'],
        ['morphology', 'graph_retrieval'],
        ['national', 'technology'],
        ['verification', 'risk_assessment', 'risk_verification'],
        ['revision', 'reverification', 'risk_revision', 'risk_reverification', 'final'],
      ];
      document.querySelectorAll('.agent-step').forEach((e, i) => {
        const values = groups[i].map((k) => run.stages?.[k]).filter(Boolean);
        e.classList.toggle('running', values.includes('running'));
        e.classList.toggle(
          'complete',
          values.length > 0 && values.every((v) => ['complete', 'skipped'].includes(v))
        );
      });
      if (run.last_event)
        $('#workflow-events').textContent =
          (workflowLabels[run.last_event.stage] || run.last_event.stage) +
          ' · ' +
          (workflowStatus[run.last_event.status] || run.last_event.status) +
          ' · ' +
          (run.last_event.detail || '');
      $('#workflow-resume').hidden = !['paused', 'failed'].includes(run.status);
      $('#workflow-start').disabled = ['queued', 'running'].includes(run.status);
    }
    async function loadWorkflow(id, detail = false) {
      try {
        if (detail) {
          renderWorkflow(await api('/api/workflows/' + id));
          return;
        }
        const data = await api('/api/workflows' + (id ? '/' + id : '') + '?view=status');
        const run = id ? data : data.runs?.[0];
        if (run) {
          if (run.version !== activeWorkflow?.version) renderWorkflowStatus(run);
          if (['queued', 'running'].includes(run.status))
            scheduleStatus('workflow', () => loadWorkflow(run.id));
          else stopStatus('workflow');
        }
      } catch (e) {
        $('#workflow-events').textContent = e.message;
        retryStatus('workflow', () => loadWorkflow(id), e);
      }
    }

    $('#workflow-start').addEventListener('click', () =>
      action($('#workflow-start'), async () => {
        const r = await api('/api/workflows?' + query(), {
          limit: 8,
          question: $('#question').value || $('#hypothesis').value,
        });
        renderWorkflow(r.run);
        loadWorkflow(r.run.id);
        notify(
          '최대 8개 뉴스로 역할별 분석·검토 사이클을 시작했습니다. 각 단계의 산출물을 확인할 수 있습니다.'
        );
      })
    );
    $('#workflow-refresh').addEventListener('click', () => loadWorkflow(activeWorkflow?.id));
    $('#workflow-resume').addEventListener('click', () =>
      action($('#workflow-resume'), async () => {
        if (activeWorkflow) {
          const r = await api('/api/workflows/' + activeWorkflow.id + '/resume', {});
          renderWorkflow(r.run);
          loadWorkflow(r.run.id);
        }
      })
    );

    return {
      load: loadWorkflow,
      details: () => activeWorkflow && loadWorkflow(activeWorkflow.id, true),
    };
  };
})();
