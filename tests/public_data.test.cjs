const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const hashed = n => 'public-data-' + n.toString(16).padStart(64, '0') + '.json';
const bucket = id => {let h=2166136261;for(const b of new TextEncoder().encode(id))h=Math.imul(h^b,16777619)>>>0;return(h&255).toString(16).padStart(2,'0');};
function setup(fetch) {
  const context = {window: {}, fetch, TextEncoder};
  vm.runInNewContext(fs.readFileSync('static/public-data.js','utf8'), context);
  return context.window.PublicData;
}

test('high-degree graph filters and limits before neighbor hydration, keeps complete connection labels', async () => {
  const found=new Map();for(let i=0;found.size<256;i++)found.set(bucket('node-'+i),'node-'+i);
  const root='root', nodes=[root,...found.values()].map((id,i)=>({id,title:id,type:'source',_order:i}));
  const edges=[...found.values()].map((id,i)=>({id:'edge-'+i,source:root,target:id,layer:'provenance'}));
  const parts=Object.fromEntries([...found.keys()].map((key,i)=>[key,hashed(i+1)]));
  const calls=[];
  const api=setup(async path=>({ok:true,json:async()=>{
    if(path==='site-manifest.json')return {schema_version:1,version:'a',graph:{parts,meta:{}}};
    calls.push(path);const key=Object.keys(parts).find(key=>parts[key]===path);
    const incident=edges.filter(e=>bucket(e.source)===key||bucket(e.target)===key);
    return {nodes:nodes.filter(n=>bucket(n.id)===key),edges:incident,neighbors:Object.fromEntries(nodes.map(n=>[n.id,n]))};
  }}));
  const one=await api.graphView({id:root,limit:1,layer:'no-match'});
  assert.equal(one.shown,1);assert.equal(calls.length,1);assert.equal(one.detail.connections.length,0);
  const selected=await api.graphView({id:root,limit:2});
  assert.equal(selected.shown,2);assert.ok(calls.length<=2);assert.equal(selected.detail.connections.length,256);
  assert.ok(selected.detail.connections.every(e=>e.other.title));
});

test('expired generation restarts the whole operation once with fresh index; missing same generation fails', async()=>{
  let manifests=0, stale=0;
  const a={schema_version:1,version:'a',news:{index:hashed(1),parts:{0:hashed(2)}}};
  const b={schema_version:1,version:'b',news:{index:hashed(3),parts:{0:hashed(4)}}};
  const api=setup(async(path,options)=>{
    if(path==='site-manifest.json'){assert.equal(options.cache,'no-store');manifests++;return {ok:true,json:async()=>manifests===1?a:b};}
    if(path===hashed(2)){stale++;return {ok:false,status:404};}
    return {ok:true,json:async()=>path===hashed(4)?[{id:'article',title:'updated'}]:[['article','index','','','',0,'0']]};
  });
  assert.equal((await api.articles(['article']))[0].title,'updated');
  assert.equal(manifests,2);assert.equal(stale,1);
  const missing=setup(async path=>path==='site-manifest.json'?{ok:true,json:async()=>a}:{ok:false,status:404});
  await assert.rejects(missing.news(),e=>e.status===404);
});

test('completed shard cache is bounded and in-flight requests are deduplicated', async()=>{
  const nodes=Array.from({length:70},(_,i)=>({id:'n'+i,part:hashed(i+1)}));
  let requests=0;
  const parts=Object.fromEntries(nodes.map((n,i)=>[String(i),n.part]));
  const m={schema_version:1,version:'v',news:{index:hashed(1000),parts}};
  const api=setup(async path=>({ok:true,json:async()=>{
    if(path==='site-manifest.json')return m;
    if(path===m.news.index)return nodes.map((n,i)=>[n.id,n.id,'','','',0,String(i)]);
    requests++;return [{id:nodes.find(n=>n.part===path).id}];
  }}));
  await Promise.all([api.articles(['n0']),api.articles(['n0'])]);assert.equal(requests,1);
  for(const n of nodes.slice(1))await api.articles([n.id]);
  await api.articles(['n0']);assert.equal(requests,71);
});
