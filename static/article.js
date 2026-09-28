'use strict';
const url = new URLSearchParams(location.search).get('url') || '';
const $ = (s) => document.querySelector(s),
  node = (tag, text) => {
    const e = document.createElement(tag);
    if (text) e.textContent = text;
    return e;
  };
const labels = {
  summary: '한 문장 요약',
  facts: '핵심 내용',
  novelty: '무엇이 달라졌나',
  meaning: '이 뉴스가 중요한 이유',
  application: '독자가 얻는 것',
  network_changes: '연결된 관측 결과의 변화',
  uncertainty: '판단에 중요한 조건',
  signals: '예정된 후속 변화',
};
const statuses = {
  missing: '아직 생성한 해설이 없습니다.',
  queued: '해설 생성 대기 중',
  reading: 'Agent-Reach로 원문을 확보하고 있습니다.',
  analyzing: '원문·관측 결과 분석 및 독립 검토 중',
  reviewing: '독립 근거 검토 중',
  complete: '근거·편집 검토 통과',
  stale: '해설 기준 또는 연결 근거가 바뀌었습니다. 해설을 갱신해 주세요.',
  needs_review: '해설 보완 필요',
  failed: '생성 실패',
  interrupted: '생성 준비 또는 재시작 필요',
  insufficient: '원문 근거 부족',
};
const kinds = {
  fact: '확인된 원문 내용',
  interpretation: '조건부 해석',
  limitation: '확인 한계',
  proposal: '후속 제안',
};
const polling = Workspace.poller((e) => {
  $('#status').textContent = e.message;
});
function safeLink(text, href) {
  const a = node('a', text);
  try {
    const u = new URL(href, location.origin);
    if (['http:', 'https:'].includes(u.protocol)) {
      a.href = u.href;
      a.rel = 'noopener noreferrer';
    }
  } catch (_) {}
  return a;
}
try {
  const u = new URL(url);
  if (['http:', 'https:'].includes(u.protocol)) $('#source').href = u.href;
} catch (_) {}
if (url) {
  const nav = document.querySelector('main>nav');
  if (nav) {
    nav.append(
      document.createTextNode(' · '),
      safeLink('이 기사를 연결한 지식 위키', '/wiki?source_url=' + encodeURIComponent(url)),
      document.createTextNode(' · '),
      safeLink('공통 지식 지도', '/knowledge#source_url=' + encodeURIComponent(url))
    );
  }
}
async function api(method = 'GET') {
  return Workspace.request(
    '/api/article-explanations' + (method === 'GET' ? '?url=' + encodeURIComponent(url) : ''),
    method === 'GET' ? {} : { body: { url } }
  );
}
function render(data) {
  $('#status').textContent =
    (statuses[data.status] || data.status) +
    (data.updated_at ? ' · ' + new Date(data.updated_at).toLocaleString() : '') +
    (data.error ? '\n' + data.error : '');
  const busy = ['queued', 'reading', 'analyzing', 'reviewing'].includes(data.status);
  $('#generate').disabled = busy;
  $('#generate').textContent = data.status === 'missing' ? '상세 해설 생성' : '해설 다시 확인·생성';
  $('#result').replaceChildren();
  if (data.result) {
    const r = data.result;
    $('#result').append(
      node('h2', r.title),
      node(
        'p',
        `원문 ${r.body_characters.toLocaleString()}자 중 ${r.analyzed_characters.toLocaleString()}자 분석${r.body_truncated ? ' · 수집 본문 길이 제한' : ''} · ${new Date(r.generated_at).toLocaleString()}`
      )
    );
    for (const [key, label] of Object.entries(labels)) {
      if (!r.sections[key]?.length) continue;
      const section = node('section');
      section.append(node('h2', label));
      for (const [i, s] of (r.sections[key] || []).entries()) {
        const text = i === 0 ? s.text.replace(/^(또한|아울러|그리고)\s+/, '') : s.text;
        section.append(node('small', kinds[s.kind]), node('p', text));
        const detail = node('details');
        detail.append(node('summary', '인용 근거 확인'));
        for (const ref of s.evidence_ids) {
          const e = r.evidence.find((e) => e.id === ref);
          if (!e) continue;
          detail.append(
            e.source_url
              ? safeLink(e.title, e.source_url)
              : node('strong', e.title + ' · 관측 집계')
          );
          if (e.char_start != null)
            detail.append(node('small', ` · 본문 ${e.char_start}–${e.char_end}자`));
          detail.append(node('blockquote', e.text));
        }
        section.append(detail);
      }
      $('#result').append(section);
    }
    renderMap(r.observation);
  }
  polling.stop('article');
  if (busy) polling.schedule('article', load, 3000);
}
function renderMap(o) {
  const section = node('details');
  section.className = 'observation-details';
  section.append(
    node('summary', '관계 지도와 관측 수치 자세히 보기'),
    node('p', o.method),
    node('p', o.coverage)
  );
  if (!o.nodes.length)
    section.append(
      node('p', '현재 관측 지도 표본에서 이 기사에 직접 연결된 주제를 찾지 못했습니다.')
    );
  else {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 800 300');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', '기사에 연결된 전략 키워드 관계 지도');
    const positions = new Map(
      o.nodes.map((n, i) => [
        n.id,
        {
          x: 400 + 290 * Math.cos((i * 2 * Math.PI) / o.nodes.length),
          y: 150 + 105 * Math.sin((i * 2 * Math.PI) / o.nodes.length),
        },
      ])
    );
    function shape(tag, attrs) {
      const e = document.createElementNS(svg.namespaceURI, tag);
      for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
      svg.append(e);
      return e;
    }
    for (const e of o.edges) {
      const a = positions.get(e.source),
        b = positions.get(e.target);
      if (a && b)
        shape('line', { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: '#a4bbad', 'stroke-width': 2 });
    }
    for (const n of o.nodes) {
      const p = positions.get(n.id);
      shape('circle', {
        cx: p.x,
        cy: p.y,
        r: 7,
        fill: o.direct_node_ids.includes(n.id) ? '#246c52' : '#7e9589',
      });
      shape('text', { x: p.x, y: p.y - 12, 'text-anchor': 'middle', 'font-size': 12 }).textContent =
        n.label;
    }
    section.append(
      svg,
      node('p', `${o.days[0]} ~ ${o.days.at(-1)} · 연결선은 같은 문서·날짜의 공동 관측입니다.`)
    );
    const table = node('table'),
      head = node('tr');
    [
      '주제·키워드',
      `선택한 ${o.comparison_days || 7}일`,
      `직전 ${o.comparison_days || 7}일`,
      '일별 고유 문서 수',
    ].forEach((t) => head.append(node('th', t)));
    table.append(head);
    for (const n of o.nodes) {
      const row = node('tr');
      row.append(
        node('td', n.label),
        node('td', String(n.current)),
        node('td', String(n.previous)),
        node('td', n.series.join(' → '))
      );
      table.append(row);
    }
    section.append(table);
    const changes = node('details');
    changes.append(node('summary', `관계별 선택 기간·직전 ${o.comparison_days || 7}일 변화`));
    const names = new Map(o.nodes.map((n) => [n.id, n.label]));
    for (const c of o.changes || []) {
      if (!c.source) continue;
      changes.append(
        node(
          'p',
          `${names.get(c.source) || c.source} ↔ ${names.get(c.target) || c.target}: ${c.previous} → ${c.current}건 (${c.delta > 0 ? '+' : ''}${c.delta})${c.percent === null ? ' · 이전 0건: 증감률 계산 불가' : ` · ${c.percent}%`}${c.sparse ? ' · 소표본' : ''}`
        )
      );
    }
    section.append(changes);
  }
  section.append(safeLink('관측 지도에서 더 보기', '/observatory'));
  $('#result').append(section);
}
async function load() {
  try {
    render(await api());
  } catch (e) {
    $('#status').textContent = e.message;
  }
}
$('#generate').addEventListener('click', async () => {
  try {
    $('#generate').disabled = true;
    render(await api('POST'));
  } catch (e) {
    $('#status').textContent = e.message;
    $('#generate').disabled = false;
  }
});
load();
