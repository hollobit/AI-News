(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  async function load() {
    $('#remote-open').hidden = true;
    $('#remote-refresh').disabled = true;
    $('#remote-status').textContent = '접속 정보를 확인하고 있습니다.';
    try {
      const response = await fetch('remote-access.json?t=' + Date.now(), { cache: 'no-store' });
      if (!response.ok) throw Error('접속 정보를 불러올 수 없습니다. 잠시 후 새로고침해 주세요.');
      const data = await response.json();
      if (!data.origin)
        throw Error(
          '원격 연결이 아직 준비되지 않았습니다. 공개 뉴스와 검토된 분석은 기존 사이트에서 이용할 수 있습니다.'
        );
      const url = new URL(data.origin);
      if (
        url.protocol !== 'https:' ||
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        url.pathname !== '/'
      )
        throw Error('접속 주소를 확인할 수 없습니다.');
      url.pathname = '/context';
      for (const key of ['q', 'question', 'answer', 'forecast']) {
        const value = new URLSearchParams(location.search).get(key);
        if (value) url.searchParams.set(key, value);
      }
      $('#remote-open').href = url.href;
      $('#remote-open').hidden = false;
      $('#remote-address').textContent = '접속 주소 · ' + data.origin;
      $('#remote-status').textContent =
        '로그인 후 관계 탐색·자연어 분석·전망 생성 및 운영 기능을 사용할 수 있습니다.';
      $('#remote-mode').textContent =
        data.mode === 'temporary'
          ? '현재는 임시 HTTPS 연결입니다. 재연결 시 주소가 바뀔 수 있으므로 이 페이지를 즐겨찾기하세요. 연결이 끊겼다면 접속 정보를 새로고침해 주세요.'
          : '연결 서버가 중지되면 원격 분석도 일시 중단됩니다.';
    } catch (error) {
      $('#remote-status').textContent = error.message;
      $('#remote-address').textContent = '';
    } finally {
      $('#remote-refresh').disabled = false;
    }
  }
  $('#remote-refresh').addEventListener('click', load);
  load();
})();
