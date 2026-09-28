(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const kinds = {topic:'주제',entity:'대상',concept:'개념',event:'사건',issue:'쟁점',question:'질문'};
  const statuses = {pending:'생성 대기',running:'종합·검토 중',complete:'검토 완료',stale:'원근거 변경 · 재검토 필요',empty:'해당 근거 없음',needs_review:'독립 검토 미통과',failed:'생성 실패',interrupted:'중단 후 재개 대기'};
  const relations = {involves:'참여·관련',supports:'뒷받침',contrasts:'차이·상충',updates:'후속 변경'};
  let selected = new URL(location.href).searchParams.get('id') || '', request = 0, data = null, timer;
  let lastRender='';
  const sourceFilter=new URL(location.href).searchParams.get('source_url')||'';
  const href = id => '/wiki?id=' + encodeURIComponent(id);
  const safeURL = url => {try {const u=new URL(url);return ['https:','http:'].includes(u.protocol)?u.href:'';}catch{return '';}};
  const date = value => value ? new Date(value).toLocaleString('ko-KR') : '아직 없음';
  const api=(url,body)=>Workspace.request(url,{body});

  function references(ids) {return '<div class="refs">'+ids.map(id=>'<a href="#source-'+esc(id)+'">근거 '+esc(id.slice(-8))+'</a>').join('')+'</div>';}
  function renderDetail() {
    const p=data.page;
    if(!p) {
      const topic=data.topics.find(t=>t.id===selected.split(':')[0]);
      $('detail').innerHTML='<h2>'+esc(topic?.title || '주제를 선택해 주세요')+'</h2><p class="empty">'+(topic?.status==='stale'?'원근거가 변경되어 이전 설명을 현재 지식으로 표시하지 않습니다. 재검토 후 새 판이 공개됩니다.':'공개된 페이지를 선택해 원근거와 연결 내용을 확인하세요. 미검토 초안은 표시하지 않습니다.')+'</p>'+history();return;
    }
    const links=data.links.filter(l=>l.source===p.id||l.target===p.id);
    const names=new Map(data.pages.map(x=>[x.id,x.title]));
    for(const l of data.links){names.set(l.source,l.source_title||names.get(l.source)||l.source);names.set(l.target,l.target_title||names.get(l.target)||l.target);}
    $('detail').innerHTML='<span class="tag">'+esc(kinds[p.kind])+'</span><h2>'+esc(p.title)+'</h2><p class="meta">판 '+p.revision+' · '+esc(date(p.updated_at))+'</p><p class="meta">'+esc(p.scope)+'</p>'+
      '<p class="meta">페이지 ID: '+esc(p.id)+' · <a href="/knowledge#id='+encodeURIComponent(p.id)+'">공통 지식 지도에서 보기</a></p>'+(data.aliases||[]).filter(a=>a.source===p.id||a.target===p.id).map(a=>'<p>사용자 별칭: <a data-page href="'+href(a.source===p.id?a.target:a.source)+'">'+esc(a.source===p.id?a.target:a.source)+'</a> — '+esc(a.reason)+' (독립 검토 관계 아님)</p>').join('')+
      p.claims.map(c=>'<section class="claim"><span class="tag">'+esc({reported:'출처의 보도·주장',interpretation:'검토된 해석',uncertain:'미확인·쟁점'}[c.kind])+'</span><p>'+esc(c.text)+'</p>'+references(c.evidence_ids)+'</section>').join('')+
      '<h3>연결된 내용</h3>'+(links.map(l=>'<div class="relation"><a data-page href="'+href(l.source)+'">'+esc(names.get(l.source)||l.source)+'</a> → <span class="tag">'+esc(relations[l.relation])+'</span> → <a data-page href="'+href(l.target)+'">'+esc(names.get(l.target)||l.target)+'</a><p>'+esc(l.text)+'</p>'+references(l.evidence_ids)+'</div>').join('') || '<p>현재 검토된 추가 연결이 없습니다.</p>')+
      '<h3>원근거</h3>'+p.evidence.map(e=>'<section class="source" id="source-'+esc(e.id)+'"><strong>'+esc(e.title)+'</strong><p class="meta">'+esc(e.origin)+' · '+esc(e.day)+' · '+esc(e.scope)+'</p>'+(safeURL(e.source_url)?'<a target="_blank" rel="noopener noreferrer" href="'+esc(safeURL(e.source_url))+'">원출처 열기</a>':'<span class="meta">외부 원문 URL 없음</span>')+'<details><summary>실제로 사용한 발췌</summary><p>'+esc(e.text)+'</p></details></section>').join('')+history();
  }
  function history(){return '<h3>갱신 이력</h3><p class="meta">과거 판의 설명은 당시 기록이며 현재 유효한 지식을 뜻하지 않습니다.</p>'+((data.history||[]).map(h=>'<details><summary class="meta">판 '+h.id+' · '+esc(date(h.created_at))+' · '+esc(statuses[h.status]||h.status)+' · 근거 추가 '+h.changes.added.length+' / 변경 '+h.changes.changed.length+' / 제외 '+h.changes.removed.length+'</summary>'+['claims_added','claims_removed'].map(k=>(h.changes[k]||[]).map(t=>'<p>'+esc(k==='claims_added'?'추가: ':'제외: ')+esc(t)+'</p>').join('')).join('')+'</details>').join('')||'<p class="empty">기록 없음</p>');}
  function renderPages() {
    const q=$('search').value.trim().toLocaleLowerCase();
    const pages=data.pages.filter(p=>p.title.toLocaleLowerCase().includes(q));
    $('pages').innerHTML=pages.map(p=>'<a data-page href="'+href(p.id)+'" '+(p.id===selected?'aria-current="page"':'')+'><small>'+esc(kinds[p.kind])+'</small> '+esc(p.title)+'</a>').join('')||'<p class="empty">표시할 페이지가 없습니다.</p>';
  }
  async function load() {
    const token=++request;
    try {
      const params=new URLSearchParams();if(selected)params.set('id',selected);if(sourceFilter)params.set('source_url',sourceFilter);
      const result=await api('/api/wiki?'+params);
      if(token!==request)return;data=result;
      $('notice').textContent=result.method+(result.enabled?' · 변경 근거는 10분 주기로 확인합니다.':' · 외부 분석이 꺼져 있습니다.');
      const signature=JSON.stringify(result);if(signature===lastRender)return;lastRender=signature;
      $('topics').innerHTML=data.topics.map(t=>'<section class="topic"><h2><a data-page href="'+href(t.id)+'">'+esc(t.title)+'</a></h2><p class="meta">'+esc(t.config?.enabled===false?'비활성화':t.status==='stale'?statuses.stale:statuses[t.job_status]||t.job_status)+'<br>마지막 작업: '+esc(date(t.updated_at))+'</p>'+(t.error?'<p class="meta">'+esc(t.error)+'</p>':'')+'<button data-refresh="'+esc(t.id)+'" '+(!data.enabled||t.config?.enabled===false||t.job_status==='running'?'disabled':'')+'>근거 확인·갱신</button> <button data-edit="'+esc(t.id)+'">설정</button></section>').join('');
      renderPages();renderDetail();
    }catch(e){if(token===request)$('notice').textContent='조회 실패: '+e.message;}
  }
  document.addEventListener('click',async event=>{
    const edit=event.target.closest('[data-edit]');
    if(edit){const t=data.topics.find(t=>t.id===edit.dataset.edit);const f=$('topic-form');if(!t.config)return;for(const k of ['topic','name','terms'])f.elements[k].value=k==='topic'?t.id:k==='terms'?t.config.terms.join(', '):t.config.name;for(const k of ['enabled','historical'])f.elements[k].checked=Boolean(t.config[k]);f.closest('details').open=true;f.scrollIntoView({block:'center'});return;}
    const link=event.target.closest('a[data-page]');
    if(link&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey){event.preventDefault();selected=new URL(link.href).searchParams.get('id');historyPush();await load();return;}
    const button=event.target.closest('[data-refresh]');if(!button)return;
    button.disabled=true;
    try{await api('/api/wiki',{topic:button.dataset.refresh});$('notice').textContent='갱신 요청을 등록했습니다. 종합과 독립 검토 후 공개됩니다.';}
    catch(e){$('notice').textContent=e.message;}finally{button.disabled=false;}
  });
  function historyPush(){window.history.pushState({},'',href(selected));}
  addEventListener('popstate',()=>{selected=new URL(location.href).searchParams.get('id')||'';load();});
  $('search').addEventListener('input',()=>data&&renderPages());
  for(const [id,action] of [['topic-form','configure'],['alias-form','alias']]){
    $(id).addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');button.disabled=true;const payload=Object.fromEntries(new FormData(form));payload.action=action;
      if(action==='configure'){payload.terms=payload.terms.split(',').map(s=>s.trim()).filter(Boolean);payload.enabled=form.elements.enabled.checked;payload.historical=form.elements.historical.checked;}else payload.remove=form.elements.remove.checked;
      try{await api('/api/wiki',payload);lastRender='';await load();$('notice').textContent='설정을 저장했습니다. 새 설명은 독립 검토 후 공개됩니다.';}catch(e){$('notice').textContent=e.message;}finally{button.disabled=false;}
    });
  }
  function schedule(){clearInterval(timer);if(!document.hidden)timer=setInterval(load,15000);}
  document.addEventListener('visibilitychange',()=>{schedule();if(!document.hidden)load();});
  load();schedule();
})();
