/* Shared request and lifecycle primitives; POST requests are never retried. */
(() => {
  'use strict';
  async function request(url, {body, signal, timeout, cache} = {}) {
    const cached = body === undefined ? cache?.get(url) : null;
    const headers = body === undefined ? {} : {'Content-Type': 'application/json'};
    if (cached?.etag) headers['If-None-Match'] = cached.etag;
    if (timeout) signal = signal ? AbortSignal.any([signal, AbortSignal.timeout(timeout)]) : AbortSignal.timeout(timeout);
    const response = await fetch(url, {method: body === undefined ? 'GET' : 'POST', headers,
      body: body === undefined ? undefined : JSON.stringify(body), signal});
    if (response.status === 304 && cached) return cached.data;
    let data;
    try { data = await response.json(); }
    catch (_) { const error = Error('응답을 읽지 못했습니다. 다시 시도하세요.'); error.status = response.status; throw error; }
    if (!response.ok) { const error = Error([data.error,data.detail,data.message].find(value=>typeof value==='string'&&value)||`요청 실패 (${response.status})`); error.status = response.status; throw error; }
    if (cache && body === undefined) {
      cache.set(url, {etag: response.headers.get('ETag'), data});
      if (cache.size > 40) cache.delete(cache.keys().next().value);
    }
    return data;
  }
  function poller(onError = () => {}) {
    const tasks = new Map(), running = new Set();
    let closed = false;
    async function run(name, task) {
      if (closed || document.hidden || running.has(name) || tasks.get(name) !== task) return;
      running.add(name);
      try { await task.fn(); task.failures = 0; }
      catch (error) {
        task.failures++;
        onError(error);
        if (tasks.get(name) === task) task.timer = setTimeout(() => run(name, task), Math.min(60000, task.delay * 2 ** Math.min(task.failures, 5)));
      } finally {
        running.delete(name);
        const next = tasks.get(name);
        if (next && next !== task && !closed && !document.hidden) {
          clearTimeout(next.timer);
          next.timer = setTimeout(() => run(name, next), next.delay);
        }
      }
    }
    function stop(name) { clearTimeout(tasks.get(name)?.timer); tasks.delete(name); }
    function schedule(name, fn, delay = 5000) {
      stop(name);
      const task = {fn, delay, failures: 0}; tasks.set(name, task);
      if (!closed && !document.hidden) task.timer = setTimeout(() => run(name, task), delay);
    }
    function visibility() {
      for (const [name, task] of tasks) {
        clearTimeout(task.timer);
        if (!document.hidden) task.timer = setTimeout(() => run(name, task), 0);
      }
    }
    document.addEventListener('visibilitychange', visibility);
    function close() { closed = true; for (const name of tasks.keys()) stop(name); document.removeEventListener('visibilitychange', visibility); }
    addEventListener('pagehide', close, {once: true});
    return {schedule, stop, close};
  }
  function safeURL(value, base = location.origin) {
    try { const u = new URL(value, base); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch (_) { return ''; }
  }
  const statusLabels = {pending: '대기', preparing: '준비 중', queued: '대기', running: '처리 중', finishing: '현재 작업 마무리 중',
    paused: '일시중지', complete: '완료', verified: '검토 통과', stale: '입력 변경 · 재검토 필요', needs_review: '검토 필요', requires_review: '검토 필요', failed: '실패'};
  window.Workspace = {request, poller, safeURL, statusLabels, questionTimeout: 210000};
})();
