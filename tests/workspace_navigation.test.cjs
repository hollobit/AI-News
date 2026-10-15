const test = require('node:test');
const assert = require('node:assert/strict');
const nav = require('../static/workspace-navigation.js');

test('every former live tool, including review subviews, has exactly one destination', () => {
  assert.equal(nav.spaces.length, 8);
  assert.equal(new Set(nav.entries.map(e => e.id)).size, nav.entries.length);
  const paths = ['/context','/strategy','/news','/archive','/observatory','/papers','/wiki','/knowledge','/intelligence','/risks','/graph','/research','/simulation','/operations','/workflow','/sources'];
  paths.push(...['events','topics','decisions','scenarios','research','operations','concepts','experiments','profiles','risk_history'].map(p=>'/intelligence/'+p));
  for(const path of paths) assert.equal(new URL(nav.byId[nav.identify('https://live.test'+path)].path, 'https://live.test').pathname,path);
  for(const s of nav.spaces) assert.equal(nav.byId[s.home].space,s.id);
});

test('public legacy destinations retain their real meaning rather than borrowing live ids', () => {
  assert.equal(nav.identify('https://public.test/AI-News/index.html?view=archive', true),'timeline');
  assert.equal(nav.identify('https://public.test/AI-News/index.html?view=research', true),'news');
  assert.equal(nav.identify('https://public.test/AI-News/knowledge.html', true),'knowledge');
  assert.equal(nav.identify('https://live.test/archive'),'archive');
  assert.match(nav.href('archive',true),/context.html\?destination=archive/);
  assert.match(nav.href('graph',true),/destination=graph/);
  assert.equal(nav.href('knowledge',true),'knowledge.html');
  for(const view of ['__proto__','constructor','not-a-view']) assert.equal(nav.identify('https://public.test/index.html?view='+view,true),'services');
});

test('queries, semantic date names and supported observation windows cross the boundary', () => {
  const c=nav.context('https://public.test/AI-News/index.html?view=news&q=AI%20%26%20의료&day=2026-10-11&topic=연구');
  assert.equal(new URL(nav.href('news',false,c),'https://live.test').searchParams.get('date'),'2026-10-11');
  assert.equal(new URL(nav.href('news',true,c),'https://public.test').searchParams.get('day'),'2026-10-11');
  const bridge=nav.href('simulation',true,c);
  const live=new URL(nav.remoteURL('https://live.test',bridge));
  assert.equal(live.pathname,'/simulation');assert.equal(live.searchParams.get('q'),'AI & 의료');assert.equal(live.searchParams.get('topic'),'연구');
  assert.equal(new URL(nav.href('observatory',true,{window:'90'}),'https://public.test').searchParams.get('window'),'90');
  assert.equal(new URL(nav.href('papers',false,c),'https://live.test').searchParams.has('date'),false);
  assert.equal(new URL(nav.href('papers',false,c),'https://live.test').searchParams.get('ws_date'),'2026-10-11');
});

test('a long analysis question survives navigation without submitting an invalid news query', () => {
  const q='한글 자연어 질의 '.repeat(160).slice(0,2000);
  const news=nav.href('news',true,{q});const u=new URL(news,'https://public.test');
  assert.equal(u.searchParams.has('q'),false);assert.equal(nav.context(u.href).q,q.trim());
  const c=new URL(nav.remoteURL('https://live.test',nav.href('context',true,nav.context(u.href))));
  assert.equal(c.searchParams.get('q'),q.trim());
});

test('only allowlisted paths and HTTPS origins can become remote handoffs', () => {
  for(const origin of ['http://live.test','https://api.trycloudflare.com','https://www.trycloudflare.com','https://user:pass@live.test','https://live.test/path','https://live.test/?token=secret','javascript:alert(1)']) assert.throws(()=>nav.remoteURL(origin,'context.html'));
  for(const id of ['//evil.test','https://evil.test','../../private','constructor','__proto__']) assert.throws(()=>nav.remoteURL('https://live.test','context.html?destination='+encodeURIComponent(id)));
  const u=new URL(nav.remoteURL('https://live.test','context.html?destination=operations&next=https://evil.test&token=SECRET'));
  assert.equal(u.pathname,'/operations');assert.equal(u.search,'');
});

test('exact source URLs, saved questions, graph view and forecast identity survive explicit handoff', () => {
  const source='https://news.test/a?article=17&redirect=https%3A%2F%2Fsource.test%2F%3Fid%3D9#part';
  const bridge=nav.gateway('article',{},'https://public.test/?'+new URLSearchParams({url:source}));
  assert.equal(new URL(nav.remoteURL('https://live.test',bridge)).searchParams.get('url'),source);
  const q=new URL(nav.remoteURL('https://live.test','context.html?q=AI&answer=saved-id'));
  assert.equal(q.searchParams.get('answer'),'saved-id');
  const edited=new URL(nav.remoteURL('https://live.test','context.html?q=edited&question=previous'));
  assert.equal(edited.searchParams.get('q'),'edited');assert.equal(edited.searchParams.has('question'),false);
  assert.equal(new URL(nav.remoteURL('https://live.test','context.html?question=legacy')).searchParams.get('q'),'legacy');
  const forecast=new URL(nav.remoteURL('https://live.test','context.html?destination=forecast&forecast=saved-forecast&q=AI'));
  assert.equal(forecast.hash,'#forecast-panel');assert.equal(forecast.searchParams.get('forecast'),'saved-forecast');
  const graph=new URL(nav.remoteURL('https://live.test',nav.gateway('knowledge', {q:'의료'}, 'https://public.test/knowledge.html#id=source%3Apaper%3A123&view=3d&dist=72')));
  const hash=new URLSearchParams(graph.hash.slice(1));assert.equal(hash.get('id'),'source:paper:123');assert.equal(hash.get('view'),'3d');assert.equal(hash.get('dist'),'72');assert.equal(hash.get('q'),'의료');
  const updated=new URL(nav.snapshotHref('knowledge',{q:'edited'},'https://live.test/knowledge#q=previous&id=medical'),'https://public.test');
  assert.equal(new URLSearchParams(updated.hash.slice(1)).get('q'),'edited');
});

test('switching to a compatible snapshot retains selections without leaking unrelated ids', () => {
  const obs=new URL(nav.snapshotHref('observatory',{window:'90'},'https://live.test/observatory?id=medical&expand=1'),'https://public.test');
  assert.equal(obs.searchParams.get('id'),'medical');assert.equal(obs.searchParams.get('window'),'90');assert.equal(obs.searchParams.get('expand'),'1');
  const paper=new URL(nav.snapshotHref('papers',{},'https://live.test/papers?paper=2609.01234'),'https://public.test');
  assert.equal(paper.searchParams.get('id'),'2609.01234');
  const news=new URL(nav.snapshotHref('news',{q:'AI'},'https://live.test/news?id=private-row&token=SECRET'),'https://public.test');
  assert.equal(news.searchParams.get('q'),'AI');assert.equal(news.searchParams.has('id'),false);assert.equal(news.searchParams.has('token'),false);
});
