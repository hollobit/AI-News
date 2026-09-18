(() => {'use strict';
const $=id=>document.getElementById(id), el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const names={strategy:'전략 대시보드',news:'뉴스',archive:'날짜별 아카이브',observatory:'관측 지도',research:'뉴스 분석',risks:'위험·조건부 시나리오',papers:'논문',wiki:'지식 위키',graph:'관계 탐색',sources:'출처 목록',services:'서비스 안내'};
let view=new URLSearchParams(location.search).get('view')||'strategy',site,wiki,obs,limit=40;
if(!names[view])view='services';
$('search').value=new URLSearchParams(location.search).get('q')||'';
const url=(v)=>v==='observatory'?'observatory.html':v==='graph'?'knowledge.html':'index.html?view='+v;
const link=(text,href)=>{const a=el('a',text);a.href=href;return a;};
for(const [key,name] of Object.entries(names)){const a=link(name,url(key));if(view===key)a.setAttribute('aria-current','page');$('menu').append(a);}
$('title').textContent=names[view];document.title=names[view]+' · AI 뉴스';
function safe(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}}
function external(text,value){const u=safe(value);if(!u)return el('span','공개 출처 링크 없음');const a=link(text,u);a.target='_blank';a.rel='noopener noreferrer';return a;}
function card(title,meta){const n=el('article',undefined,'card');n.append(el('small',meta,'tag'),el('h2',title));return n;}
function article(a){const n=card(a.title,`${a.day} · ${a.topic} · ${a.analyses.length?'현재 입력·독립 검토 확인':'제목·출처만 공개'}`);for(const r of a.analyses){const d=el('details');d.append(el('summary',r.kind+(r.title?' · '+r.title:'')),el('p',r.text));if(r.uncertainty)d.append(el('p','불확실성: '+r.uncertainty));n.append(d);}const links=el('div',undefined,'links');links.append(external('원출처',a.url),link('지식 연결','knowledge.html#source_url='+encodeURIComponent(a.url)));n.append(links);return n;}
function paper(p){const n=card(p.title,`${p.id} · ${p.day} · ${p.status} · ${p.provider}`);if(p.summary)n.append(el('p',p.summary));for(const c of p.claims){const d=el('details');d.append(el('summary',c.title),el('p',c.detail),el('p','불확실성: '+c.uncertainty));n.append(d);}n.append(external('논문 원출처',p.url),document.createTextNode(' · '),link('지식 연결','knowledge.html#id='+encodeURIComponent('source:paper:'+p.id)));return n;}
function wikiPage(p){const n=card(p.title,p.kind+' · 검토 판 '+p.revision);for(const c of p.claims){n.append(el('p',c.text));for(const id of c.evidence_ids){const source=wiki.nodes.find(n=>n.id===id);if(source?.url)n.append(external('인용 출처 · '+source.title,source.url));}}const node=wiki.nodes.find(n=>(n.page_ids||[]).includes(p.id));if(node)n.append(link('관계 탐색','knowledge.html#id='+encodeURIComponent(node.id)));return n;}
function matching(item){const q=$('search').value.trim().toLocaleLowerCase();return (!q||JSON.stringify(item).toLocaleLowerCase().includes(q))&&(!$('day').value||item.day===$('day').value)&&(!$('topic').value||item.topic===$('topic').value);}
function render(){const box=$('content');box.replaceChildren();let items=[],draw=article;
 if(view==='services'){box.append(card('공개 웹에서 가능한 기능','조회·검색·필터·내려받기'),el('p','뉴스와 검토된 기본·심층 분석, 날짜별 아카이브, 14·30·90일 관측 곡선·관계 지도·히트맵·확장·날짜 재생, 검토 논문, 위키와 인용 관계 탐색을 제공합니다.'),card('로컬 서버가 필요한 기능','공개본에서는 실행하지 않음'),el('p','뉴스 수집·원문 확보·LLM 질문 생성·재분석·주제 설정 변경·사건 병합·결정 편집·시뮬레이션 실행·내부 운영 로그는 공개 사이트에 연결하지 않습니다. 질문 대신 공개 검토 자료를 검색할 수 있습니다. 시뮬레이션 초안과 미검토 해석은 공개 사실로 게시하지 않습니다.'));$('result-count').textContent='';$('more').hidden=true;return;}
 if(view==='strategy'){
 const grid=el('div',undefined,'grid');for(const t of (obs.nodes||[]).filter(n=>n.kind==='topic'&&n.count>0)){const n=card(t.label,`선택 ${obs.comparison_days}일 고유 문서 ${t.current} · 이전 ${t.previous}${t.previous===0?' · 신규 관측/비교 한계':''}`);const bars=el('div',undefined,'bars'),max=Math.max(1,...t.series);t.series.forEach((v,i)=>{const b=el('i');b.style.height=(v/max*80)+'px';b.title=obs.days[i]+' · '+v;bars.append(b);});n.append(bars,link('곡선·근거·관계 보기','observatory.html'));grid.append(n);}box.append(grid);box.append(el('h2','최근 뉴스와 검토 분석'));items=site.news;
 }else if(view==='risks'){items=site.risks||[];draw=r=>{const n=card(r.title,r.day+' · 검토된 해석 · 현재 위험 '+r.current_severity);for(const [k,label] of [['current_basis','현재 판단 근거'],['scenario','조건부 미래 시나리오'],['assumptions','전제'],['uncertainty','불확실성'],['mitigations','완화 방안']]){n.append(el('h3',label),el('p',Array.isArray(r[k])?r[k].join('\n'):r[k]||''));}n.append(external('인용 출처',r.url),document.createTextNode(' · '),link('관련 뉴스 분석','index.html?view=news&id='+r.article_id));return n;};}
 else if(view==='papers'){items=site.papers;draw=paper;}
 else if(view==='wiki'){items=wiki.pages;draw=wikiPage;}
 else if(view==='sources'){const map=new Map(site.news.filter(a=>a.url).map(a=>[a.url,a]));items=[...map.values()];draw=a=>{const n=card(a.title,a.day);n.append(external(a.url,a.url));return n;};}
 else items=site.news.filter(a=>view!=='research'||a.analyses.length);
 items=items.filter(matching).filter(a=>!$('reviewed').checked||['wiki','risks'].includes(view)||a.analyses?.length||a.status==='검토 완료');
 const requested=new URLSearchParams(location.search).get('id');if(requested)items=items.filter(i=>i.id===requested);
 $('result-count').textContent=`공개 자료 ${items.length.toLocaleString()}개 · ${Math.min(limit,items.length)}개 표시`;
 let day='';for(const item of items.slice(0,limit)){if(view==='archive'&&item.day!==day){day=item.day;box.append(el('h2',day));}box.append(draw(item));}$('more').hidden=limit>=items.length;
}
async function load(){try{[site,wiki,obs]=await Promise.all(['site.json','knowledge.json','observatory-14-default.json'].map(async path=>{const r=await fetch(path);if(!r.ok)throw Error('공개 자료 조회 실패');return r.json();}));$('notice').textContent='읽기 전용 공개 스냅샷 · '+new Date(site.exported_at).toLocaleString('ko-KR')+' · 원문 발췌 제외 · 전체 분석 완료를 뜻하지 않습니다.';for(const [key,label] of [['news','고유 뉴스'],['reviewed_news','검토 분석 연결 뉴스'],['papers','메타데이터 확보 논문'],['reviewed_papers','현재 검토 논문']]){const n=el('article',undefined,'metric');n.append(el('small',label),el('strong',site.coverage[key].toLocaleString()));$('metrics').append(n);}const items=view==='papers'?site.papers:site.news;for(const day of [...new Set(items.map(a=>a.day).filter(Boolean))].sort().reverse())$('day').add(new Option(day,day));for(const topic of [...new Set(items.map(a=>a.topic).filter(Boolean))].sort())$('topic').add(new Option(topic,topic));render();}catch(e){$('notice').textContent=e.message;}}
for(const id of ['search','day','topic','reviewed'])$(id).addEventListener(id==='search'?'input':'change',()=>{limit=40;render();});$('more').onclick=()=>{limit+=40;render();};$('download').onclick=()=>{const a=link('내려받기','site.json');a.download='AI-News-public.json';a.click();};load();
})();
