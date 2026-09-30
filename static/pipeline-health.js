(() => {
  'use strict';
  if (location.pathname !== '/operations') return;
  const host = document.querySelector('#pipeline-health');
  const status = document.querySelector('#pipeline-health-state');
  const el = (tag, text, parent) => {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  };
  const date = (value) => (value ? new Date(value).toLocaleString('ko-KR') : '기록 없음');
  const labels = {
    complete: '완료',
    running: '진행 중',
    waiting: '다음 묶음 대기',
    paused: '중지',
    requires_review: '검토 필요',
    needs_review: '검토 필요',
    verified: '검증 완료',
    pending: '대기',
    failed: '실패',
  };
  let refreshing = false;
  let stagesSignature = null;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const data = await Workspace.request('/api/operations/health', { timeout: 15000 });
      status.textContent =
        data.monitor_stale || !data.checked_at || Date.now() - Date.parse(data.checked_at) > 15000
          ? '모니터 기록 지연 · 정기 점검 실행을 확인하세요'
          : `마지막 점검 ${date(data.checked_at)} · 실시간 연결 · 2초 자동 갱신`;
      const stages = host.querySelector('[data-pipeline-stages]');
      const signature = JSON.stringify(data.stages);
      if (signature !== stagesSignature) {
        stagesSignature = signature;
        stages.replaceChildren();
        for (const stage of data.stages) {
          const card = el('article', '', stages);
          el('h3', stage.name, card);
          el('strong', labels[stage.status] || stage.status, card);
          el('p', stage.model, card);
          el('p', `갱신 ${date(stage.updated_at)}`, card);
          if (stage.last_progress_at)
            el('p', `최근 단계 진행 ${date(stage.last_progress_at)}`, card);
          if (stage.count != null) el('p', `고유 뉴스 ${stage.count.toLocaleString()}건`, card);
          if (stage.counts)
            el(
              'p',
              Object.entries(stage.counts)
                .map(([key, value]) => `${labels[key] || key} ${value.toLocaleString()}`)
                .join(' · '),
              card
            );
        }
      }
      host.querySelector('[data-pipeline-models]').textContent = Object.entries(data.models || {})
        .map(([key, value]) => `${key}: ${value}`)
        .join(' · ');
      host.querySelector('[data-pipeline-schedulers]').textContent = Object.entries(
        data.schedulers || {}
      )
        .map(
          ([key, value]) =>
            `${key === 'collection' ? '기본 분석 예약' : '상세 분석 예약'}: ${value.stage || '기록 없음'}${value.error_code ? ' · ' + value.error_code : ''} (${date(value.checked_at)})`
        )
        .join(' / ');
      try {
        const usage = await Workspace.request('/api/runtime/analysis', { timeout: 15000 });
        const calls = host.querySelector('[data-pipeline-calls]');
        calls.replaceChildren();
        for (const call of usage.recent.slice(0, 8))
          el(
            'p',
            `${call.model || '모델 기록 없음'} · ${call.role} · ${labels[call.status] || call.status}${call.error_code ? ' · ' + call.error_code : ''}`,
            calls
          );
      } catch (_) {
        host.querySelector('[data-pipeline-calls]').textContent = '실제 모델 호출 기록 조회 실패';
      }
      const incidents = host.querySelector('[data-pipeline-incidents]');
      const focusedIncident = document.activeElement?.dataset.incident;
      incidents.replaceChildren();
      if (!data.incidents.length) el('p', '감지된 문제가 없습니다.', incidents);
      for (const incident of data.incidents) {
        const item = el('article', '', incidents);
        item.className = 'pipeline-incident';
        el(
          'strong',
          `${incident.resolved_at ? '해소' : incident.acknowledged_at ? '확인함 · 미해소' : '확인 필요'} · ${incident.message}`,
          item
        );
        el('p', incident.action, item);
        el(
          'small',
          `발생 ${date(incident.opened_at)} · 최근 감지 ${date(incident.last_seen_at)}${incident.resolved_at ? ' · 해소 ' + date(incident.resolved_at) : ''}`,
          item
        );
        el(
          'p',
          incident.notification_status === 'requested'
            ? 'macOS 알림 요청 완료'
            : incident.notification_status === 'failed'
              ? 'macOS 알림 요청 실패 · 운영 화면에서 확인하세요'
              : 'macOS 알림 대기',
          item
        );
        if (!incident.acknowledged_at && !incident.resolved_at) {
          const button = el('button', '확인했습니다', item);
          button.className = 'button';
          button.dataset.incident = String(incident.id);
          if (focusedIncident === String(incident.id)) button.focus({ preventScroll: true });
          button.onclick = async () => {
            button.disabled = true;
            try {
              await Workspace.request(`/api/operations/incidents/${incident.id}/ack`, {
                body: {},
                timeout: 15000,
              });
              await refresh();
            } catch (error) {
              status.textContent = error.message;
              button.disabled = false;
            }
          };
        }
      }
    } catch (error) {
      status.textContent = `상태 조회 실패: ${error.message}`;
    } finally {
      refreshing = false;
      poller.schedule('pipeline', refresh, 2000);
    }
  }
  const poller = Workspace.poller();
  host.querySelector('[data-pipeline-refresh]').onclick = refresh;
  refresh();
})();
