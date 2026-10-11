/* An allowlisted handoff to the same live application, never a second engine. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s),
    nav = WorkspaceNavigation;
  const params = new URLSearchParams(location.search);
  const destination = params.get('destination') || 'context';
  const entry = nav.byId[destination];
  const space = nav.spaces.find((s) => s.id === entry?.space);
  let origin = '';
  const query = $('#remote-query');
  $('#remote-open').dataset.remoteDestination = destination;
  query.value = nav.context(location.href).q;
  $('#remote-title').textContent = entry?.label || '작업 공간 연결';
  $('#remote-subtitle').textContent =
    space?.description || '공개 자료에서 실시간 분석으로 이어갑니다.';
  $('#remote-space').textContent = space?.label || 'CONNECTED WORKSPACE';
  const features = $('#remote-features');
  features.replaceChildren();
  for (const e of nav.entries.filter((e) => e.space === entry?.space && e.id !== 'article')) {
    const section = document.createElement('section'),
      title = document.createElement('h2'),
      detail = document.createElement('p'),
      a = document.createElement('a');
    title.textContent = e.label;
    detail.textContent = e.description;
    a.textContent = '이 기능 열기 →';
    a.href = nav.href(e.id, true, nav.context(location.href));
    a.dataset.remoteDestination = e.id;
    section.append(title, detail, a);
    features.append(section);
  }
  const preview = entry?.snapshot
    ? destination
    : { graph: 'knowledge', research: 'news', forecast: 'risks' }[destination] || 'strategy';
  $('#remote-preview').dataset.remoteDestination = preview;
  function update() {
    const u = new URL(location.href);
    if (query.value.trim()) u.searchParams.set('q', query.value.trim());
    else u.searchParams.delete('q');
    u.searchParams.delete('ws_query');
    history.replaceState(null, '', u);
    $('#remote-preview').href = nav.snapshotHref(
      preview,
      nav.context(location.href),
      preview === destination ? location.href : ''
    );
    for (const a of features.querySelectorAll('[data-remote-destination]'))
      a.href = nav.href(a.dataset.remoteDestination, true, {
        ...nav.context(location.href),
        q: query.value,
      });
    if (origin) {
      try {
        $('#remote-open').href = nav.remoteURL(origin, location.href);
        $('#remote-open').hidden = false;
      } catch (error) {
        $('#remote-status').textContent = error.message;
        $('#remote-open').hidden = true;
      }
    }
  }
  query.addEventListener('input', update);
  $('#remote-query-form').addEventListener('submit', (event) => {
    event.preventDefault();
    update();
    if (!$('#remote-open').hidden) location.assign($('#remote-open').href);
  });
  async function load() {
    origin = '';
    $('#remote-open').hidden = true;
    $('#remote-refresh').disabled = true;
    $('#remote-status').textContent = '작업 공간의 접속 정보를 확인하고 있습니다.';
    try {
      const response = await fetch('remote-access.json?t=' + Date.now(), {
        cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw Error('접속 정보를 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.');
      const data = await response.json();
      if (!data.origin)
        throw Error('실시간 연결이 준비되지 않았습니다. 공개 자료는 계속 이용할 수 있습니다.');
      nav.remoteURL(data.origin, location.href);
      origin = data.origin;
      update();
      $('#remote-open').textContent = '로그인하고 ' + (entry?.label || '작업 공간') + ' 열기 ↗';
      $('#remote-status').textContent =
        '같은 서버의 최신 자료와 기능으로 이어집니다. 로그인 후 선택한 화면이 열립니다.';
      $('#remote-address').textContent = data.origin;
      $('#remote-mode').textContent =
        data.mode === 'temporary'
          ? '임시 연결 · 주소가 바뀌면 이 페이지에서 다시 연결할 수 있습니다.'
          : '연결된 서버가 실행 중일 때 이용할 수 있습니다.';
    } catch (error) {
      $('#remote-status').textContent =
        error.name === 'TimeoutError'
          ? '접속 정보 확인이 지연되고 있습니다. 다시 확인해 주세요.'
          : error.message;
      $('#remote-address').textContent = '';
    } finally {
      $('#remote-refresh').disabled = false;
    }
  }
  $('#remote-refresh').addEventListener('click', load);
  update();
  load();
})();
