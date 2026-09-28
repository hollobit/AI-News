/* Shared request and lifecycle primitives; POST requests are never retried. */
(() => {
  'use strict';
  async function request(url, { body, signal, timeout, cache } = {}) {
    const cached = body === undefined ? cache?.get(url) : null;
    const headers = body === undefined ? {} : { 'Content-Type': 'application/json' };
    if (cached?.etag) headers['If-None-Match'] = cached.etag;
    // Status reads have a bound; durable job submissions keep the server's
    // preparation deadline unless the caller explicitly requests a timeout.
    if (timeout === undefined && body === undefined) timeout = 90000;
    if (timeout)
      signal = signal
        ? AbortSignal.any([signal, AbortSignal.timeout(timeout)])
        : AbortSignal.timeout(timeout);
    const response = await fetch(url, {
      method: body === undefined ? 'GET' : 'POST',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    if (response.status === 304 && cached) return cached.data;
    let data;
    try {
      data = await response.json();
    } catch (_) {
      const error = Error('응답을 읽지 못했습니다. 다시 시도하세요.');
      error.status = response.status;
      throw error;
    }
    if (!response.ok) {
      const error = Error(
        [data.error, data.detail, data.message].find(
          (value) => typeof value === 'string' && value
        ) || `요청 실패 (${response.status})`
      );
      error.status = response.status;
      throw error;
    }
    if (cache && body === undefined) {
      cache.set(url, { etag: response.headers.get('ETag'), data });
      if (cache.size > 40) cache.delete(cache.keys().next().value);
    }
    return data;
  }
  function poller(onError = () => {}) {
    const tasks = new Map(),
      running = new Set();
    let closed = false,
      suspended = false;
    const retryable = (error) =>
      !error.status || error.status === 408 || error.status === 429 || error.status >= 500;
    const delayFor = (task) => Math.min(60000, task.delay * 2 ** Math.min(task.failures, 5));
    function arm(name, task, delay) {
      clearTimeout(task.timer);
      if (!closed && !suspended && !document.hidden)
        task.timer = setTimeout(() => run(name, task), delay);
    }
    async function run(name, task) {
      if (closed || suspended || document.hidden || running.has(name) || tasks.get(name) !== task)
        return;
      running.add(name);
      try {
        await task.fn();
      } catch (error) {
        onError(error);
        if (tasks.get(name) === task) retry(name, task.fn, error, task.delay);
      } finally {
        running.delete(name);
        const next = tasks.get(name);
        if (next && next !== task) arm(name, next, delayFor(next));
      }
    }
    function stop(name) {
      clearTimeout(tasks.get(name)?.timer);
      tasks.delete(name);
    }
    function schedule(name, fn, delay = 5000) {
      stop(name);
      if (closed) return;
      const task = { fn, delay, failures: 0 };
      tasks.set(name, task);
      arm(name, task, delay);
    }
    function retry(name, fn, error, delay = 5000) {
      const failures = (tasks.get(name)?.failures || 0) + 1;
      stop(name);
      if (closed || !retryable(error)) return;
      const task = { fn, delay, failures };
      tasks.set(name, task);
      arm(name, task, delayFor(task));
    }
    function visibility() {
      for (const [name, task] of tasks) {
        clearTimeout(task.timer);
        arm(name, task, task.failures ? delayFor(task) : 0);
      }
    }
    document.addEventListener('visibilitychange', visibility);
    function close() {
      closed = true;
      for (const name of tasks.keys()) stop(name);
      document.removeEventListener('visibilitychange', visibility);
      removeEventListener('pagehide', pagehide);
      removeEventListener('pageshow', pageshow);
    }
    function pagehide(event) {
      if (!event.persisted) return close();
      suspended = true;
      for (const task of tasks.values()) clearTimeout(task.timer);
    }
    function pageshow(event) {
      if (event.persisted) {
        suspended = false;
        visibility();
      }
    }
    addEventListener('pagehide', pagehide);
    addEventListener('pageshow', pageshow);
    return { schedule, retry, stop, close };
  }
  function safeURL(value, base = location.origin) {
    try {
      const u = new URL(value, base);
      return ['http:', 'https:'].includes(u.protocol) ? u.href : '';
    } catch (_) {
      return '';
    }
  }
  function currentNavigation(path = location.pathname) {
    const hash = location.hash || '#overview';
    for (const link of document.querySelectorAll('.sidebar nav a')) {
      const target = new URL(link.href, location.origin);
      const selected = target.pathname === path && (!target.hash || target.hash === hash);
      if (selected) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
      link.classList.toggle('active', selected);
    }
  }
  const statusLabels = {
    pending: '대기',
    preparing: '준비 중',
    queued: '대기',
    running: '처리 중',
    finishing: '현재 작업 마무리 중',
    paused: '일시중지',
    complete: '완료',
    verified: '검토 통과',
    stale: '입력 변경 · 재검토 필요',
    needs_review: '검토 필요',
    requires_review: '검토 필요',
    failed: '실패',
  };
  window.Workspace = {
    request,
    poller,
    safeURL,
    currentNavigation,
    statusLabels,
    questionTimeout: 210000,
  };
})();
