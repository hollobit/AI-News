/* Topics panel; initialized by the workbench composition root. */
(() => {
  'use strict';
  window.StrategyPanels ||= {};
  window.StrategyPanels.topics = (context) => {
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
    const topicStates = {
      emerging: '새롭게 관측',
      growing: '증가',
      cooling: '둔화',
      stable: '유지',
      dormant: '미관측',
      needs_review: '검토 필요',
    };
    let registryData = null,
      registrySignature = '',
      registryVisible = 30;
    function topicBadges(item) {
      const area = node('div', 'dynamic-badges');
      area.append(
        node(
          'span',
          'pill',
          item.origin === 'builtin' ? '기본' : item.origin === 'manual' ? '수동 등록' : '자동 제안'
        )
      );
      if (item.status) area.append(node('span', 'pill', topicStates[item.status] || item.status));
      return area;
    }
    function topicEvidence(parent, item) {
      const details = node('details');
      details.append(node('summary', '', '관측 표현과 근거 확인'));
      if (item.terms?.length) details.append(node('p', '', '관측 표현: ' + item.terms.join(', ')));
      details.append(
        node(
          'p',
          'subtle',
          item.verification_label ||
            '수집 근거의 표현을 규칙으로 검토한 관측 항목입니다. 인과관계의 확정을 뜻하지 않습니다.'
        )
      );
      const evidence = item.evidence || [];
      if (!evidence.length)
        details.append(
          node(
            'p',
            'subtle',
            '현재 표시할 연결 근거가 없습니다. 0건은 해당 주제가 존재하지 않는다는 뜻이 아닙니다.'
          )
        );
      evidence.slice(0, 4).forEach((e) => {
        details.append(
          link(e.title || e.label || '관측 근거', e.source_url || e.url || '/news?date=all')
        );
        const quote = e.source_quotes || e.source_quote || e.quotes || e.quote;
        if (quote) details.append(node('p', 'subtle', improvementText(quote)));
        if (e.caution) details.append(node('p', 'subtle', e.caution));
      });
      parent.append(details);
    }
    function renderDynamicEvidence(data) {
      if (!$('#dynamic-evidence-panel').open) return;
      const area = $('#dynamic-topic-evidence');
      area.replaceChildren();
      visibleLenses(data.trends?.lenses || [])
        .filter((t) => t.dynamic)
        .forEach((item) => {
          const card = node('article', 'dynamic-evidence-card');
          card.append(
            topicBadges(item),
            node('h4', '', item.label || item.name),
            node('p', '', item.description || item.subtitle || ''),
            node(
              'p',
              'subtle',
              `최근 ${num(item.current)}건 · 직전 ${num(item.previous)}건${item.current === 0 ? ' · 미관측' : ''}`
            )
          );
          topicEvidence(card, item);
          const actions = node('div', 'heading-actions'),
            select = node('button', 'button', '이 주제의 뉴스 보기'),
            exclude = node('button', 'button', '관측 제외');
          select.addEventListener('click', () => selectLens(item.id));
          exclude.addEventListener('click', () => changeTopic(exclude, item.id, 'exclude'));
          actions.append(select, exclude);
          card.append(actions);
          area.append(card);
        });
    }
    function renderRegistry(data) {
      if (!$('#topic-management').open) return;
      const area = $('#topic-registry');
      area.replaceChildren();
      const live = [
        ...(state.overview?.trends?.lenses || []),
        ...(state.overview?.trends?.monitoring?.topics || []),
        ...(state.overview?.trends?.monitoring?.candidates || []),
      ];
      const search = $('#topic-registry-search').value.trim().toLocaleLowerCase();
      const filtered = (data.items || [])
        .filter(
          (r) =>
            !search ||
            [r.label, r.name, ...(r.terms || r.metadata?.terms || [])]
              .filter(Boolean)
              .join(' ')
              .toLocaleLowerCase()
              .includes(search)
        )
        .slice()
        .sort((a, b) => (b.origin === 'manual') - (a.origin === 'manual'));
      $('#topic-registry-count').textContent =
        `${num(filtered.length)}개 중 ${num(Math.min(registryVisible, filtered.length))}개 표시`;
      $('#topic-registry-more').hidden = registryVisible >= filtered.length;
      filtered.slice(0, registryVisible).forEach((record) => {
        const item = {
            ...(record.metadata || {}),
            ...record,
            ...live.find((i) => i.id === record.id),
          },
          excluded = Boolean(record.excluded);
        const card = node('article', 'topic-registry-item' + (excluded ? ' excluded' : ''));
        card.append(
          topicBadges(item),
          node('h4', '', item.label || item.name || item.id),
          node(
            'p',
            'subtle',
            `${['signal', 'signals'].includes(record.kind || item.kind) ? '변화 신호' : '전략 주제'} · ${excluded ? '자동 재등장 제외 중' : '관측 중'}`
          )
        );
        if (item.description) card.append(node('p', '', item.description));
        topicEvidence(card, item);
        const button = node('button', 'button', excluded ? '복원' : '제외');
        button.addEventListener('click', () =>
          changeTopic(button, item.id, excluded ? 'restore' : 'exclude')
        );
        card.append(button);
        area.append(card);
      });
      if (!area.childElementCount)
        empty(
          area,
          search
            ? '검색 조건에 맞는 관리 항목이 없습니다.'
            : '추가된 자동·수동 주제가 아직 없습니다. 직접 등록하거나 새 근거가 쌓이면 관측할 수 있습니다.'
        );
    }
    async function loadRegistry(force = false) {
      if (!$('#topic-management').open) return;
      try {
        const data = await api('/api/strategy/topics');
        registryData = data;
        const signature = String(data.version || JSON.stringify(data.items || []));
        if (force || signature !== registrySignature) {
          registrySignature = signature;
          renderRegistry(data);
        }
      } catch (e) {
        $('#topic-registry').textContent = e.message;
      }
    }
    async function changeTopic(button, id, change) {
      await action(button, async () => {
        await api('/api/strategy/topics/' + encodeURIComponent(id) + '/' + change, {});
        if (change === 'exclude' && state.lens === id) state.lens = '';
        await Promise.all([loadOverview(true), load()]);
        notify(
          change === 'exclude'
            ? '주제를 제외했습니다. 자동 재등장을 막으며 관리 목록에서 복원할 수 있습니다.'
            : '주제를 복원했습니다.'
        );
      });
    }
    $('#signals-more').addEventListener('click', () => {
      reading.showAllSignals = !reading.showAllSignals;
      if (reading.monitoringData) renderMonitoring(reading.monitoringData);
    });
    $('#candidates-more').addEventListener('click', () => {
      reading.showAllCandidates = !reading.showAllCandidates;
      if (reading.monitoringData) renderMonitoring(reading.monitoringData);
    });
    $('#dynamic-evidence-panel').addEventListener('toggle', () => {
      if ($('#dynamic-evidence-panel').open && state.overview)
        renderDynamicEvidence(state.overview);
    });
    $('#topics-more').addEventListener('click', () => {
      reading.showAllTopics = !reading.showAllTopics;
      if (state.overview) {
        renderTrends(state.overview);
        renderDynamicEvidence(state.overview);
        if (state.data && !state.newsLoading) renderNews(state.data);
      }
    });
    $('#topic-management').addEventListener('toggle', () => {
      if ($('#topic-management').open) loadRegistry(true);
    });
    $('#topic-registry-search').addEventListener('input', () => {
      registryVisible = 30;
      if (registryData) renderRegistry(registryData);
    });
    $('#topic-registry-more').addEventListener('click', () => {
      registryVisible += 30;
      if (registryData) renderRegistry(registryData);
    });
    $('#topic-add').addEventListener('click', () => $('#topic-dialog').showModal());
    $('#topic-close').addEventListener('click', () => $('#topic-dialog').close());
    $('#topic-registry-refresh').addEventListener('click', () => loadRegistry(true));
    $('#topic-form').addEventListener('submit', (e) => {
      e.preventDefault();
      action($('#topic-save'), async () => {
        const terms = [
          ...new Set(
            $('#topic-terms')
              .value.split(',')
              .map((t) => t.trim())
              .filter(Boolean)
          ),
        ];
        if (!terms.length) throw new Error('관측할 표현을 하나 이상 입력해 주세요.');
        await api('/api/strategy/topics', {
          kind: $('#topic-kind').value,
          label: $('#topic-label').value.trim(),
          terms,
          description: $('#topic-description').value.trim(),
        });
        const addedName = $('#topic-label').value.trim();
        $('#topic-dialog').close();
        $('#topic-form').reset();
        $('#topic-registry-search').value = addedName;
        registryVisible = 30;
        await Promise.all([loadOverview(true), load()]);
        notify('주제·신호를 등록했습니다. 실제 관측 건수와 근거를 확인하세요.');
      });
    });

    return {
      load: loadRegistry,
      badges: topicBadges,
      render: renderRegistry,
      renderEvidence: renderDynamicEvidence,
      get data() {
        return registryData;
      },
    };
  };
})();
