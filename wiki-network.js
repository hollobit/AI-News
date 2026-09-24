(() => {
  'use strict';
  const $=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
  const labels={topic:'주제',entity:'대상',concept:'개념',event:'사건',issue:'쟁점',question:'질문',claim:'주장',source:'원자료'};
  const palette=['#72d6c4','#8cadff','#cda3ec','#eeb773','#ee8e9e','#aad586','#a2bac8','#76bade'];
  const colors=Object.fromEntries(Object.keys(labels).map((k,i)=>[k,palette[i]]));
  const layerNames={semantic:'검토된 의미 관계',provenance:'원자료·주장 연결',recommendation:'탐색 추천'};
  const staticMode=document.documentElement.dataset.mode==='static';
  let data=null,snapshot=null,selected='',limit=100,request=0,zoom=1,panX=0,panY=0,drag=null,suppressClick=false,viewMode='3d',cameraPose={},atlas3dUnavailable=false;
  const positions=new Map();
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const svgEl=(tag,attrs)=>{const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  const safeURL=value=>{try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}};
  function saveURL(push=true){const p=new URLSearchParams();if(selected){p.set('id',selected);const prior=new URLSearchParams(location.hash.slice(1));for(const k of ['article_id','source_url'])if(prior.has(k))p.set(k,prior.get(k));}if($('search').value)p.set('q',$('search').value);if($('layer').value!=='all')p.set('layer',$('layer').value);if($('color').value!=='type')p.set('color',$('color').value);if(limit!==100)p.set('limit',String(limit));if(viewMode==='2d')p.set('view','2d');if(zoom!==1||panX||panY){p.set('zoom',zoom.toFixed(3));p.set('x',panX.toFixed(1));p.set('y',panY.toFixed(1));}if(viewMode==='3d'&&window.atlas3D?.ready){const pose=window.atlas3D.pose(),defaults={az:.18,el:.28,dist:26,tx:0,ty:0,tz:0};if(Object.keys(pose).some(key=>pose[key]!==defaults[key]))for(const [key,value] of Object.entries(pose))p.set(key,String(value));}const u=new URL(location.href);u.hash=p.toString();if(u.href!==location.href)history[push?'pushState':'replaceState'](null,'',u);}
  function setMode(mode,save=false){viewMode=mode==='2d'||atlas3dUnavailable?'2d':'3d';if(viewMode==='3d'&&window.atlas3D&&!window.atlas3D.setActive(true))viewMode='2d';if(viewMode==='2d')window.atlas3D?.setActive(false);$('mode3d').setAttribute('aria-pressed',String(viewMode==='3d'));$('mode2d').setAttribute('aria-pressed',String(viewMode==='2d'));$('navigation-hint').textContent=viewMode==='3d'?'3D: 드래그 회전 · 휠/두 손가락 확대 · 노드 선택':'2D: 드래그 이동 · 휠 확대 · 노드 선택';document.querySelector('.canvas-panel').classList.toggle('is-3d',viewMode==='3d');$('network').setAttribute('aria-hidden',String(viewMode==='3d'));$('atlas3d').setAttribute('aria-hidden',String(viewMode==='2d'));if(viewMode==='3d'&&data)window.atlas3D?.render(data,selected,$('color').value);if(save)saveURL();}
  function restoreURL(){const p=new URLSearchParams(location.hash.slice(1));selected=p.get('id')||'';$('search').value=p.get('q')||'';$('layer').value=['all','semantic','provenance','recommendation'].includes(p.get('layer'))?p.get('layer'):'all';$('color').value=p.get('color')==='community'?'community':'type';limit=Math.max(10,Math.min(500,Number(p.get('limit'))||100));zoom=Math.max(.3,Math.min(4,Number(p.get('zoom'))||1));panX=Math.max(-10000,Math.min(10000,Number(p.get('x'))||0));panY=Math.max(-10000,Math.min(10000,Number(p.get('y'))||0));cameraPose=Object.fromEntries(['az','el','dist','tx','ty','tz'].filter(k=>p.has(k)).map(k=>[k,p.get(k)]));setMode(p.get('view'));if(viewMode==='3d')window.atlas3D?.restore(cameraPose);load();}
  function select(id){selected=id;saveURL();load();}
  function transform(){$('scene').setAttribute('transform',`translate(${panX} ${panY}) scale(${zoom})`);}
  function staticView(){
    if(!selected){const hash=new URLSearchParams(location.hash.slice(1));if(hash.has('paper_id'))selected='source:paper:'+hash.get('paper_id');if(hash.has('source_url')){const url=safeURL(hash.get('source_url'));selected=snapshot.nodes.find(n=>n.type==='source'&&n.url===url)?.id||'missing-source';}}
    const layer=$('layer').value,q=$('search').value.trim().toLocaleLowerCase();
    const edges=snapshot.edges.filter(e=>layer==='all'||e.layer===layer);let nodes=snapshot.nodes;
    if(layer!=='all'){const connected=new Set(edges.flatMap(e=>[e.source,e.target]));if(selected)connected.add(selected);nodes=nodes.filter(n=>connected.has(n.id));}
    if(selected){const ids=new Set([selected]);for(const e of edges)if(e.source===selected||e.target===selected){ids.add(e.source);ids.add(e.target);}nodes=nodes.filter(n=>ids.has(n.id));}
    // A deep link's selected neighborhood takes precedence over inherited search.
    if(q&&!selected)nodes=nodes.filter(n=>n.title.toLocaleLowerCase().includes(q));
    const total=nodes.length;nodes=[...nodes].sort((a,b)=>Number(b.id===selected)-Number(a.id===selected)).slice(0,limit);
    const ids=new Set(nodes.map(n=>n.id)),byId=new Map(snapshot.nodes.map(n=>[n.id,n]));const found=byId.get(selected);
    const detail=found?{...found,connections:snapshot.edges.filter(e=>e.source===selected||e.target===selected).map(e=>({...e,other:byId.get(e.source===selected?e.target:e.source)}))}:selected?{id:selected,type:'source',title:'현재 연결된 검토 지식 없음',scope:'이 링크의 대상은 현재 공개 스냅샷에 없습니다.',connections:[]}:null;
    return {...snapshot,nodes,edges:edges.filter(e=>ids.has(e.source)&&ids.has(e.target)),detail,total,shown:nodes.length};
  }
  let articleData=null,articleObservations=null;
  async function addArticleContext(){
    const route=new URLSearchParams(location.hash.slice(1));
    const articleId=route.get('article_id')||(selected.startsWith('source:news:')?selected.slice(12):'');
    if(snapshot.coverage.all_reviewed_documents_connected){
      if(!selected&&articleId)selected=snapshot.nodes.find(n=>n.news_id===articleId)?.id||'';
      if(selected)return;
    }
    if(!articleId&&!route.has('source_url'))return;
    if(!articleData){const r=await fetch('./site.json');if(!r.ok)throw Error('뉴스 연결 자료를 읽을 수 없습니다.');articleData=await r.json();}
    const article=articleData.news.find(a=>articleId?a.id===articleId:a.url===safeURL(route.get('source_url')));
    if(!article)return;
    if(!articleObservations){const r=await fetch('./observatory-90-expanded.json');if(!r.ok)throw Error('관측 연결 자료를 읽을 수 없습니다.');articleObservations=await r.json();}
    const source=snapshot.nodes.find(n=>n.type==='source'&&(n.news_id?n.news_id===article.id:article.url&&n.url===article.url&&n.title===article.title));
    const sid=source?.id||'source:news:'+article.id;
    const addNode=n=>{if(!snapshot.nodes.some(old=>old.id===n.id))snapshot.nodes.push(n);};
    const addEdge=(id,target,layer,text)=>{if(!snapshot.edges.some(e=>e.id===id))snapshot.edges.push({id,source:sid,target,layer,kind:layer==='provenance'?'reviewed_analysis':'observed_in_document',text,source_ids:[sid]});};
    addNode({id:sid,type:'source',title:article.title,url:article.url,day:article.day,scope:'이 뉴스의 검토 분석·위키 인용·90일 관측 연결입니다. 공동 관측은 의미 관계나 인과관계가 아닙니다.',status:'news_source'});
    for(const [i,a] of (article.analyses||[]).entries()){
      const id='news-analysis:'+article.id+':'+i;
      addNode({id,type:'claim',title:a.title||a.text,scope:a.kind+' · 현재 입력·독립 검토 확인',analysis_text:a.text,uncertainty:a.uncertainty,source_ids:[sid]});
      addEdge('news-link:'+article.id+':'+i,id,'provenance','이 뉴스의 '+a.kind+' · 원출처에 연결된 검토 결과');
    }
    const docs=new Set(Object.entries(articleObservations.documents||{}).filter(([,d])=>article.observation_document_id?d.document_id===article.observation_document_id:article.url&&d.url===article.url&&d.title===article.title).map(([id])=>id));
    for(const n of articleObservations.nodes||[]){
      if(!(n.document_ids_by_day||[]).some(ids=>ids.some(id=>docs.has(id))))continue;
      const id='news-observed:'+n.id;
      addNode({id,type:n.kind==='topic'?'topic':'concept',title:n.label,scope:'같은 뉴스에서 관측된 표현 · 의미·인과 관계 검증 아님',source_ids:[sid]});
      addEdge('news-observed-link:'+article.id+':'+n.id,id,'recommendation','90일 관측 지도에서 이 문서에 함께 관측됨 · 검토된 의미 관계 아님');
    }
    if(!selected||selected==='missing-source')selected=sid;
  }
  async function load(){
    const token=++request;
    try{
      if(staticMode){if(!snapshot){const r=await fetch('./knowledge.json');if(!r.ok)throw Error('공개 스냅샷을 읽을 수 없습니다.');snapshot=await r.json();}await addArticleContext();if(token!==request)return;data=staticView();}
      else{const p=new URLSearchParams({id:selected,q:$('search').value,layer:$('layer').value,limit:String(limit)});const hash=new URLSearchParams(location.hash.slice(1));if(!selected)for(const k of ['source_url','paper_id'])if(hash.has(k))p.set(k,hash.get(k));const r=await fetch('/api/wiki/network?'+p);const result=await r.json();if(!r.ok)throw Error(result.error||'조회 실패');if(token!==request)return;data=result;}
      if(token!==request)return;
      $('status').textContent=(staticMode?'읽기 전용 공개 스냅샷 · '+snapshot.exported_at+' · ':'')+data.method;
      const c=data.coverage;
      $('counts').textContent=`지도 노드 ${data.shown} / ${data.total}개 표시 · 종합 위키 ${c.pages}페이지 · 위키 인용 원자료 ${c.cited_sources}건`+(c.total_news!==undefined?` · 뉴스 ${c.total_news}건 중 검토 분석·연결 ${c.linked_news}건 · 연결 논문 ${c.linked_papers}건`:'');
      $('more').disabled=limit>=500||data.shown>=data.total;
      draw();renderDetail();renderIssues();
    }catch(e){if(token===request)$('status').textContent='조회 실패: '+e.message;}
  }
  function communities(){
    const parent=new Map(data.nodes.map(n=>[n.id,n.id]));const find=id=>{while(parent.get(id)!==id)id=parent.get(id);return id;};
    for(const e of data.edges)if(e.layer==='semantic'){const a=find(e.source),b=find(e.target);parent.set(b,a);}
    const groups=new Map();for(const n of data.nodes){const root=find(n.id);if(!groups.has(root))groups.set(root,groups.size);}
    return new Map(data.nodes.map(n=>[n.id,groups.get(find(n.id))]));
  }
  function draw(){
    $('nodes').replaceChildren();$('edges').replaceChildren();$('node-list').replaceChildren();$('legend').replaceChildren();
    const groups=communities(), mode=$('color').value;
    for(const [k,name] of Object.entries(labels)){const item=el('span',undefined,'legend-item'),dot=el('i');dot.style.background=colors[k];item.append(dot,document.createTextNode(name));$('legend').append(item);}
    if(mode==='community')$('legend').replaceChildren(el('p','검토된 의미 관계의 연결 성분별 색상입니다. 자동 주제 분류나 사실 판정이 아닙니다.'));
    const ids=new Set(data.nodes.map(n=>n.id)),degrees=new Map(data.nodes.map(n=>[n.id,0]));for(const e of data.edges){degrees.set(e.source,degrees.get(e.source)+1);degrees.set(e.target,degrees.get(e.target)+1);}
    const nodes=data.nodes.map((n,i)=>{if(!positions.has(n.id)){const angle=i*2.39996323;const radius=50+Math.sqrt(i/Math.max(1,data.nodes.length))*285;positions.set(n.id,{x:500+Math.cos(angle)*radius,y:370+Math.sin(angle)*radius});}return {...n,...positions.get(n.id)};});
    // A bounded deterministic force pass; no library/CDN/network dependency.
    for(let step=0;step<22;step++){
      const byId=new Map(nodes.map(n=>[n.id,n]));
      for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){const a=nodes[i],b=nodes[j],dx=a.x-b.x,dy=a.y-b.y,d2=Math.max(100,dx*dx+dy*dy),f=Math.min(1.5,120/d2);a.x+=dx*f*.06;a.y+=dy*f*.06;b.x-=dx*f*.06;b.y-=dy*f*.06;}
      for(const e of data.edges){const a=byId.get(e.source),b=byId.get(e.target),dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,f=(d-115)*.012;a.x+=dx/d*f;a.y+=dy/d*f;b.x-=dx/d*f;b.y-=dy/d*f;}
      for(const n of nodes){n.x=Math.max(30,Math.min(970,n.x));n.y=Math.max(30,Math.min(710,n.y));}
    }
    for(const n of nodes)positions.set(n.id,{x:n.x,y:n.y});
    for(const e of data.edges){if(!ids.has(e.source)||!ids.has(e.target))continue;const a=positions.get(e.source),b=positions.get(e.target);const line=svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:'edge '+e.layer,'data-source':e.source,'data-target':e.target});const title=svgEl('title',{});title.textContent=layerNames[e.layer]+': '+(e.text||e.kind);line.append(title);$('edges').append(line);}
    for(const n of nodes){const g=svgEl('g',{transform:`translate(${n.x} ${n.y})`,tabindex:0,role:'button','aria-label':(labels[n.type]||n.type)+' '+n.title,'data-id':n.id});const color=mode==='community'?palette[groups.get(n.id)%palette.length]:colors[n.type]||'#aaa';g.append(svgEl('circle',{r:Math.min(17,5+Math.sqrt(degrees.get(n.id)||0)*2),fill:color}));const text=svgEl('text',{x:12,y:4});text.textContent=n.title.length>24?n.title.slice(0,24)+'…':n.title;g.append(text);g.addEventListener('click',()=>{if(!suppressClick)select(n.id);});g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(n.id);}});g.addEventListener('mouseenter',()=>highlight(n.id));g.addEventListener('mouseleave',()=>highlight(''));$('nodes').append(g);const button=el('button',(labels[n.type]||n.type)+' · '+n.title);button.addEventListener('click',()=>select(n.id));$('node-list').append(button);}
    transform();if(viewMode==='3d')window.atlas3D?.render(data,selected,mode);
  }
  function highlight(id){const related=new Set([id]);for(const e of data.edges)if(e.source===id||e.target===id){related.add(e.source);related.add(e.target);}for(const n of $('nodes').children)n.style.opacity=!id||related.has(n.dataset.id)?'1':'.18';}
  function renderDetail(){const box=$('detail');box.replaceChildren();const n=data.detail;if(!n){box.append(el('h2','노드를 선택하세요'),el('p','위키·주장·원자료를 양방향으로 탐색합니다.'));return;}box.append(el('span',labels[n.type]||n.type,'badge'),el('h2',n.title),el('p',n.scope||'현재 입력과 독립 검토가 일치하는 위키 연결입니다.'));
    if(n.url&&safeURL(n.url)){const a=el('a','원출처 열기');a.href=safeURL(n.url);a.target='_blank';a.rel='noopener noreferrer';box.append(a);}
    if(n.analysis_text)box.append(el('p',n.analysis_text));
    if(n.uncertainty)box.append(el('p','불확실성: '+n.uncertainty));
    if(!staticMode&&n.href){const a=el('a',' 연결된 원래 화면 열기');a.href=n.href;box.append(a);}
    const summaries=staticMode?(snapshot.pages||[]).filter(p=>(n.page_ids||[]).includes(p.id)):[];
    for(const p of summaries){box.append(el('h3',p.title));for(const c of p.claims){box.append(el('p',c.text));for(const ref of c.evidence_ids){const b=el('button','인용 원자료');b.addEventListener('click',()=>select(ref));box.append(b);}}}
    if(n.excerpt){const d=el('details');d.append(el('summary','공개된 원문 발췌'),el('p',n.excerpt));box.append(d);}
    for(const e of n.connections||[]){if(!e.other)continue;const item=el('section',undefined,'connection'),b=el('button',e.other.title);b.addEventListener('click',()=>select(e.other.id));item.append(el('span',layerNames[e.layer],'badge'),b,el('p',e.text||e.kind));box.append(item);}
  }
  function renderIssues(){$('issues').replaceChildren();for(const issue of data.issues||[]){const d=el('details');d.append(el('summary',issue.kind),el('p',issue.message));const b=el('button','대상 보기');b.addEventListener('click',()=>select(issue.target));d.append(b);$('issues').append(d);}if(!data.issues?.length)$('issues').append(el('p','현재 점검 항목이 없습니다. 모든 자료 처리 완료를 뜻하지는 않습니다.'));}
  const svg=$('network');function point(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
  svg.addEventListener('pointerdown',e=>{const p=point(e);drag={id:e.target.closest('[data-id]')?.dataset.id,start:p,last:p,moved:false};suppressClick=false;svg.setPointerCapture(e.pointerId);});
  svg.addEventListener('pointermove',e=>{if(!drag)return;const p=point(e),dx=p.x-drag.last.x,dy=p.y-drag.last.y;drag.moved ||= Math.hypot(p.x-drag.start.x,p.y-drag.start.y)>4;
    if(drag.id){const pos=positions.get(drag.id);pos.x+=dx/zoom;pos.y+=dy/zoom;for(const g of $('nodes').children)if(g.dataset.id===drag.id)g.setAttribute('transform',`translate(${pos.x} ${pos.y})`);for(const line of $('edges').children){if(line.dataset.source===drag.id){line.setAttribute('x1',pos.x);line.setAttribute('y1',pos.y);}if(line.dataset.target===drag.id){line.setAttribute('x2',pos.x);line.setAttribute('y2',pos.y);}}}
    else{panX+=dx;panY+=dy;transform();}drag.last=p;});
  function end(){if(drag){suppressClick=drag.moved;drag=null;saveURL(false);setTimeout(()=>suppressClick=false,0);}}
  svg.addEventListener('pointerup',end);svg.addEventListener('pointercancel',end);
  svg.addEventListener('wheel',e=>{e.preventDefault();const p=point(e),next=Math.max(.3,Math.min(4,zoom*Math.exp(-e.deltaY*.001)));panX=p.x-(p.x-panX)*next/zoom;panY=p.y-(p.y-panY)*next/zoom;zoom=next;transform();saveURL(false);},{passive:false});
  $('fit').addEventListener('click',()=>{if(viewMode==='3d')window.atlas3D?.reset();else{zoom=1;panX=panY=0;transform();}saveURL();});$('mode3d').addEventListener('click',()=>setMode('3d',true));$('mode2d').addEventListener('click',()=>setMode('2d',true));$('reset').addEventListener('click',()=>{selected='';$('search').value='';saveURL();load();});$('more').addEventListener('click',()=>{limit=Math.min(500,limit+100);saveURL();load();});
  let timer;$('search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{selected='';saveURL();load();},180);});$('layer').addEventListener('change',()=>{saveURL();load();});$('color').addEventListener('change',()=>{saveURL();if(data)draw();});
  addEventListener('hashchange',restoreURL);addEventListener('popstate',restoreURL);
  window.atlas3DReady=()=>{setMode(viewMode);if(viewMode==='3d'){window.atlas3D.restore(cameraPose);if(data)window.atlas3D.render(data,selected,$('color').value);}};
  window.atlas3DFailed=message=>{atlas3dUnavailable=true;$('mode3d').disabled=true;setMode('2d');$('status').textContent=message;saveURL(false);};
  window.atlas3DSelect=select;window.atlas3DPoseChanged=()=>{if(viewMode==='3d')saveURL(false);};
  addEventListener('load',()=>{if(!window.atlas3D)window.atlas3DFailed('3D 지도 파일을 읽지 못해 2D 지도로 전환했습니다.');});
  if(staticMode)$('live-nav').remove();restoreURL();if(window.atlas3D)window.atlas3DReady();
})();
