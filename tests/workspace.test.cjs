const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(fetch, overrides = {}) {
  const context = {window: {}, fetch, AbortSignal, URL, Map, Set, Error,
    location: {origin: 'http://localhost'}, document: {hidden: false, addEventListener() {}, removeEventListener() {}},
    addEventListener() {}, setTimeout, clearTimeout, ...overrides};
  vm.runInNewContext(fs.readFileSync('static/workspace.js', 'utf8'), context);
  return context.window.Workspace;
}
test('ETag reuse and body errors preserve HTTP status without retrying POST', async () => {
  let calls = 0;
  const workspace = setup(async (url, options) => {
    calls++;
    if (calls === 1) return {ok: true, status: 200, headers: new Headers({ETag: 'v1'}), json: async () => ({count: 3})};
    if (calls === 2) { assert.equal(options.headers['If-None-Match'], 'v1'); return {status: 304}; }
    assert.equal(options.method, 'POST');
    return {ok: false, status: 503, json: async () => ({error: 'busy'})};
  });
  const cache = new Map();
  assert.equal((await workspace.request('/data', {cache})).count, 3);
  assert.equal((await workspace.request('/data', {cache})).count, 3);
  await assert.rejects(workspace.request('/action', {body: {}}), e => e.status === 503 && e.message === 'busy');
  assert.equal(calls, 3);
});
test('unsafe URLs cannot become interactive links', () => {
  const workspace = setup();
  assert.equal(workspace.safeURL('javascript:alert(1)'), '');
  assert.equal(workspace.safeURL('/wiki'), 'http://localhost/wiki');
});

test('polling pauses when hidden, backs off and prevents concurrent work', async () => {
  let id = 0, entered = 0, release;
  const timers = new Map(), listeners = {};
  const doc = {hidden: false, addEventListener(name, fn) {listeners[name] = fn;}, removeEventListener(name) {delete listeners[name];}};
  const workspace = setup(undefined, {
    document: doc,
    setTimeout(fn, delay) {const key = ++id; timers.set(key, {fn: () => {timers.delete(key); return fn();}, delay}); return key;},
    clearTimeout(key) {timers.delete(key);},
  });
  const polling = workspace.poller();
  polling.schedule('status', async () => {entered++; await new Promise(resolve => {release = resolve;});}, 100);
  const first = [...timers.values()][0].fn();
  doc.hidden = true; listeners.visibilitychange();
  doc.hidden = false; listeners.visibilitychange();
  await [...timers.values()].at(-1).fn();
  assert.equal(entered, 1);
  release(); await first;
  polling.schedule('status', async () => {throw Error('offline');}, 100);
  await [...timers.values()].at(-1).fn();
  assert.equal([...timers.values()].at(-1).delay, 200);
  polling.close();
  assert.equal(timers.size, 0);
  assert.equal(listeners.visibilitychange, undefined);
});
