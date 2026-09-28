/* Shared public-site search context. Explicit destination context wins. */
(() => {'use strict';
const currentQuery=()=>{const input=document.querySelector('#search, #theme-search');return input?input.value.trim():new URLSearchParams(location.hash.slice(1)).get('q')||new URLSearchParams(location.search).get('q')||'';};
function destination(href){const u=new URL(href,location.href);if(u.origin!==location.origin)return href;const file=u.pathname.split('/').pop();if(!['index.html','observatory.html','knowledge.html'].includes(file))return href;
 const p=file==='knowledge.html'?new URLSearchParams(u.hash.slice(1)):u.searchParams;
 if(p.has('q'))return u.href;
 const q=currentQuery();if(q)p.set('q',q);else p.delete('q');
 if(file==='knowledge.html')u.hash=p.toString();return u.href;}
function update(){for(const a of document.querySelectorAll('a[href]')){if(!a.dataset.originalPublicHref)a.dataset.originalPublicHref=a.getAttribute('href');a.href=destination(a.dataset.originalPublicHref);}}
document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(a)a.href=destination(a.href);},true);
document.addEventListener('input',update);document.addEventListener('change',update);addEventListener('popstate',update);addEventListener('hashchange',update);
new MutationObserver(records=>{if(records.some(r=>r.addedNodes.length))update();}).observe(document.body,{childList:true,subtree:true});update();
})();
