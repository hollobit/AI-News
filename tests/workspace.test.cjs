const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(fetch, overrides = {}) {
  const context = {window: {}, fetch, AbortSignal, URL, Map, Set, Error,
    location: {origin: 'http://localhost'}, document: {hidden: false, addEventListener() {}, removeEventListener() {}},
    addEventListener() {}, removeEventListener() {}, setTimeout, clearTimeout, ...overrides};
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

test('handled transient failures keep exponential delay and success resets it; permanent errors stop', async () => {
  let id = 0, failures = 3;
  const timers = new Map();
  const workspace = setup(undefined, {
    setTimeout(fn, delay) {const key = ++id; timers.set(key, {fn: () => {timers.delete(key); return fn();}, delay}); return key;},
    clearTimeout(key) {timers.delete(key);},
  });
  const polling = workspace.poller();
  async function status() {
    if (failures-- > 0) polling.retry('baseline', status, Object.assign(Error('busy'), {status: 503}), 100);
    else polling.schedule('baseline', status, 100);
  }
  polling.schedule('baseline', status, 100);
  for (const delay of [200, 400, 800, 100]) {
    await [...timers.values()].at(-1).fn();
    assert.equal([...timers.values()].at(-1).delay, delay);
  }
  polling.retry('baseline', status, Object.assign(Error('invalid'), {status: 400}), 100);
  assert.equal(timers.size, 0);
  polling.close();
});

test('bfcache suspension and restoration resume polling without overlapping work', async () => {
  let id = 0, calls = 0;
  const timers = new Map(), listeners = {};
  const workspace = setup(undefined, {
    addEventListener(name, fn) {listeners[name] = fn;}, removeEventListener(name) {delete listeners[name];},
    setTimeout(fn, delay) {const key = ++id; timers.set(key, {fn, delay}); return key;}, clearTimeout(key) {timers.delete(key);},
  });
  const polling = workspace.poller();
  polling.schedule('baseline', async () => {calls++;}, 100);
  listeners.pagehide({persisted: true}); assert.equal(timers.size, 0);
  listeners.pageshow({persisted: true});
  await [...timers.values()][0].fn(); assert.equal(calls, 1);
  polling.close();
});

test('route state restores defaults, preserves unrelated deep links and canonicalizes dashboard', () => {
  const nodes={search:{value:'',tagName:'INPUT'},sort:{value:'priority',tagName:'SELECT',options:[{value:'priority'},{value:'recent'}]}};
  const location={href:'http://localhost/?id=keep',origin:'http://localhost',search:'?id=keep'};
  const calls=[];
  const commit=(mode,url)=>{calls.push(mode);location.href=String(url);location.search=new URL(url).search;};
  const workspace=setup(undefined,{URLSearchParams,location,history:{replaceState:(s,t,u)=>commit('replace',u),pushState:(s,t,u)=>commit('push',u)},document:{hidden:false,getElementById:id=>nodes[id],querySelector:()=>({})}});
  const route=workspace.routeState({q:'search',sort:'sort'},{page:1});
  nodes.search.value='의료';route.write({page:2});
  assert.equal(new URL(location.href).pathname,'/strategy');
  assert.equal(new URL(location.href).searchParams.get('id'),'keep');
  nodes.sort.value='recent';route.write({page:1},'push');assert.deepEqual(calls,['replace','push']);
  location.search='?q=복원&page=3';const restored=route.read();
  assert.equal(nodes.search.value,'복원');assert.equal(nodes.sort.value,'priority');assert.equal(restored.page,'3');
});
