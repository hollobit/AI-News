(() => {
  'use strict';
  const $ = (s) => document.querySelector(s),
    node = (tag, text, cls = '') => {
      const n = document.createElement(tag);
      n.textContent = text;
      n.className = cls;
      return n;
    };
  let generation = 0;
  const names = {
    web: '웹 페이지',
    twitter: 'X / Twitter',
    github: 'GitHub',
    youtube: 'YouTube',
    rss: 'RSS / Atom',
    reddit: 'Reddit',
    facebook: 'Facebook',
    instagram: 'Instagram',
    bilibili: 'Bilibili',
    xiaohongshu: '샤오홍슈',
    linkedin: 'LinkedIn',
    boss: '채용 정보',
    xiaoyuzhou: '팟캐스트',
    v2ex: 'V2EX',
    xueqiu: '쉐추',
    exa_search: '웹 검색',
  };
  const labels = {
    queued: '읽기 대기',
    running: '원문 읽는 중',
    complete: '저장 완료',
    failed: '읽기 실패',
    interrupted: '중단됨',
  };
  function link(label, url) {
    const a = node('a', label);
    try {
      const u = new URL(url);
      if (['https:', 'http:'].includes(u.protocol)) {
        a.href = u.href;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
      }
    } catch (_) {}
    return a;
  }
  const api = (path, body) => Workspace.request(path, { body, timeout: 15000 });
  function result(job) {
    $('#read-status').textContent =
      (labels[job.status] || job.status) + (job.error ? ' · ' + job.error : '');
    const r = job.result;
    if (!r?.text) {
      $('#read-result').hidden = true;
      return;
    }
    const area = $('#read-result');
    area.hidden = false;
    const scopes = {
      repository_readme: '저장소 README',
      web_excerpt: '웹 본문 발췌',
      'post_text; video_not_transcribed': '게시물 본문 · 영상 내용은 미분석',
      video_description_only: '영상 설명만 확보 · 자막 없음',
      automatic_captions: '자동 생성 자막 포함',
      provided_captions: '제공 자막 포함',
      feed_summaries_not_full_articles: '피드 요약 · 기사 전문 아님',
    };
    area.replaceChildren(
      node('h3', r.title || '읽은 자료'),
      link('원문 열기 ↗', r.url),
      node(
        'p',
        `${scopes[r.evidence_scope] || '원문 발췌'} · ${new Date(r.fetched_at).toLocaleString('ko-KR')} · ${r.reader || '공개 원문'}`,
        'hint'
      ),
      node('pre', r.text),
      node(
        'p',
        r.full_content_hash
          ? `보관 본문 ${r.full_text_chars || 0}자 · ${r.full_text_truncated ? '수집 한도에서 잘린 본문' : '읽기 도구가 반환한 본문'} · 아래 문단 검색에서 발췌 이후도 검색합니다.`
          : r.truncated
            ? '기존 발췌만 보관된 자료입니다. 원문 복구를 실행하면 본문을 다시 확보합니다.'
            : '수집된 표현이며 사실성·인과관계는 별도 검토가 필요합니다.',
        'hint'
      )
    );
  }
  async function poll(id, token) {
    try {
      const job = await api('/api/agent-reach/jobs/' + id);
      if (token !== generation) return;
      result(job);
      if (['queued', 'running'].includes(job.status)) setTimeout(() => poll(id, token), 1500);
      else load();
    } catch (e) {
      if (token === generation) $('#read-status').textContent = e.message;
    }
  }
  async function load() {
    try {
      const d = await api('/api/agent-reach');
      $('#availability').textContent =
        `Agent-Reach ${d.version || '확인 중'} · ${d.availability || '실행 환경 준비 중'}`;
      $('#channels').replaceChildren(
        ...(d.channels || []).map((c) => {
          const el = node('div', '', 'channel' + (c.status === 'available' ? '' : ' unavailable'));
          el.append(
            node('strong', names[c.id] || c.id),
            node('span', c.status === 'available' ? '읽기 도구 연결됨' : '추가 설정 필요', 'badge'),
            node('p', c.scope)
          );
          return el;
        })
      );
      $('#jobs').replaceChildren(
        ...(d.jobs || []).map((j) => {
          const el = node('div', '', 'job'),
            button = node('button', '결과 보기');
          button.onclick = () => poll(j.id, ++generation);
          el.append(link(j.url, j.url), node('span', labels[j.status] || j.status), button);
          return el;
        })
      );
      if (!d.channels?.length) setTimeout(load, 2000);
    } catch (e) {
      $('#availability').textContent = e.message;
    }
  }
  $('#read-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const b = e.target.querySelector('button');
    b.disabled = true;
    try {
      const job = await api('/api/agent-reach/read', {
        url: $('#url').value.trim(),
        mode: $('#mode').value,
      });
      $('#read-result').hidden = true;
      $('#read-status').textContent = '읽기 대기…';
      poll(job.id, ++generation);
      load();
    } catch (e) {
      $('#read-status').textContent = e.message;
    } finally {
      b.disabled = false;
    }
  });
  load();
  const stamp = (value) => (value ? new Date(value).toLocaleString('ko-KR') : '기록 없음');
  const stateName = (value) =>
    ({
      queued: '대기',
      retry: '재시도 대기',
      running: '처리 중',
      complete: '완료',
      pending: '검토 대기',
      needs_review: '검토 미통과',
      failed: '실패',
      fetched: '확보',
      blocked: '접근 불가',
      unsupported: '지원 설정 필요',
    })[value] || value;
  async function pipeline() {
    if (document.hidden) {
      setTimeout(pipeline, 15000);
      return;
    }
    try {
      const d = await api('/api/agent-reach/pipeline');
      $('#pipeline-counts').textContent =
        `전체 본문 ${d.full_documents}개 · 외부 관측 ${d.observations}개 · 작업 대기 ${d.counts.queued || 0}, 처리 중 ${d.counts.running || 0}, 재시도 ${d.counts.retry || 0} · 변경 원문 검토 ${JSON.stringify(d.reanalysis)}`;
      $('#pipeline-jobs').replaceChildren(
        ...d.tasks.slice(0, 12).map((t) => {
          const r = node(
            'p',
            `${t.kind} · ${stateName(t.status)} · 시도 ${t.attempts}/3 · ${stamp(t.updated_at)}${t.error ? ' · ' + t.error : ''}`
          );
          return r;
        })
      );
      $('#subscriptions').replaceChildren(
        ...d.subscriptions.map((s) => {
          const box = node('article', '', 'job'),
            button = node('button', s.enabled ? '관측 일시중지' : '관측 재개');
          button.onclick = async () => {
            try {
              await api('/api/agent-reach/subscription-toggle', { id: s.id, enabled: !s.enabled });
              button.textContent = s.enabled ? '중지 요청 완료' : '재개 요청 완료';
            } catch (e) {
              $('#pipeline-message').textContent = e.message;
            }
          };
          box.append(
            link(s.label, s.url),
            node(
              'p',
              `${s.keywords || '전체 주제'} · ${s.interval_seconds / 3600}시간 간격 · 마지막 확인 ${stamp(s.last_checked)} · 성공 ${stamp(s.last_success)}${s.error ? ' · ' + s.error : ''}`
            ),
            button
          );
          return box;
        })
      );
      $('#source-health').replaceChildren(
        ...d.sources.slice(0, 15).map((s) => {
          const box = node('article', '', 'job'),
            b = node('button', '이력·문단 보기');
          b.onclick = () => showSource(s.url);
          box.append(
            link(s.url, s.url),
            node(
              'p',
              `${stateName(s.last_status)} · 확인 ${stamp(s.last_checked)} · 마지막 성공 ${stamp(s.last_success)} · ${s.scope || '범위 미기록'} · ${s.reader || '읽기 경로 미기록'}`
            ),
            node(
              'p',
              s.error
                ? `${s.error_kind} · ${s.error} · 재시도 가능 ${stamp(s.next_retry)}`
                : '원문 확보'
            ),
            b
          );
          return box;
        })
      );
    } catch (e) {
      $('#pipeline-message').textContent = e.message;
    } finally {
      setTimeout(pipeline, 15000);
    }
  }
  async function showSource(url) {
    $('#passage-url').value = url;
    try {
      const d = await api('/api/agent-reach/source?url=' + encodeURIComponent(url));
      $('#source-detail').replaceChildren(
        node('h3', '보관 버전과 읽기 기록'),
        ...d.versions.map((v) =>
          node(
            'p',
            `${stamp(v.fetched_at)} · ${v.characters}자 · ${v.scope} · 본문 ${v.hash.slice(0, 12)}${v.truncated ? ' · 수집 한도에서 잘림' : ''}`
          )
        ),
        ...d.attempts.map((a) =>
          node(
            'p',
            `${stamp(a.at)} · ${stateName(a.status)} · ${a.reader || ''} · ${a.error || ''}`
          )
        )
      );
    } catch (e) {
      $('#source-detail').textContent = e.message;
    }
  }
  $('#repair').onclick = async () => {
    const b = $('#repair');
    b.disabled = true;
    try {
      const r = await api('/api/agent-reach/repair', { limit: 50 });
      $('#pipeline-message').textContent =
        `복구 대기열 ${r.queued}건을 확인했습니다. 이미 대기 중인 URL은 중복 실행하지 않습니다.`;
    } catch (e) {
      $('#pipeline-message').textContent = e.message;
    } finally {
      b.disabled = false;
    }
  };
  $('#subscription-form').onsubmit = async (e) => {
    e.preventDefault();
    const b = e.target.querySelector('button');
    b.disabled = true;
    try {
      await api('/api/agent-reach/subscriptions', {
        url: $('#subscription-url').value,
        label: $('#subscription-label').value,
        keywords: $('#subscription-keywords').value,
        mode: $('#subscription-mode').value,
        interval_seconds: Number($('#subscription-interval').value),
      });
      $('#pipeline-message').textContent = '정기 관측을 등록했습니다.';
      e.target.reset();
    } catch (e) {
      $('#pipeline-message').textContent = e.message;
    } finally {
      b.disabled = false;
    }
  };
  $('#passage-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      const d = await api(
        '/api/agent-reach/passages?url=' +
          encodeURIComponent($('#passage-url').value) +
          '&q=' +
          encodeURIComponent($('#passage-query').value)
      );
      $('#passages').replaceChildren(
        ...d.evidence.map((p) => {
          const box = node('article', '', 'job');
          box.append(
            link(p.title, p.source_url),
            node(
              'small',
              `문단 ${p.passage_index + 1} · 본문 문자 ${p.char_start}–${p.char_end} · ${p.content_hash.slice(0, 12)}`
            ),
            node('pre', p.text)
          );
          return box;
        })
      );
      if (!d.evidence.length)
        $('#passages').textContent =
          '일치하는 보관 문단이 없습니다. 본문 확보 상태를 확인해 주세요.';
    } catch (e) {
      $('#passages').textContent = e.message;
    }
  };
  pipeline();
})();
