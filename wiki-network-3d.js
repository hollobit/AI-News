import * as THREE from './three.module.js';

// A bounded 3D projection of the same reviewed graph used by the 2D view.
const host = document.getElementById('atlas3d');
const tooltip = document.getElementById('atlas3d-tooltip');
const palette = ['#72d6c4','#8cadff','#cda3ec','#eeb773','#ee8e9e','#aad586','#a2bac8','#76bade'];
const types = ['topic','entity','concept','event','issue','question','claim','source'];
const edgeColors = {semantic:0x58ddc3, provenance:0x7fa8d8, recommendation:0xb497dc};
const clamp = (n,a,b) => Math.min(b,Math.max(a,n));
const hash = text => {let n=2166136261;for(const c of text){n^=c.charCodeAt(0);n=Math.imul(n,16777619);}return n>>>0;};
const round = n => Math.round(n*100)/100;

let renderer,scene,camera,group,geometry,starfield,observer,frame=0;
let active=false,failed=false,az=.18,el=.28,distance=26,focus=new THREE.Vector3(),desired=new THREE.Vector3(),preserveFocusOnce=false;
let nodeMeshes=[],edgeMeshes=[],labels=[],current=null,selected='',lastSelected='',hovered='',dragged=false;
const fingers=new Map();
const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();

function makeStars(){
  const points=[];for(let i=0;i<360;i++){const a=i*2.39996323,r=28+(i%17)*2.4,z=((hash(String(i))%3000)/3000-.5)*90;points.push(Math.cos(a)*r,Math.sin(a)*r,z);}
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(points,3));
  starfield=new THREE.Points(g,new THREE.PointsMaterial({color:0x7db6cb,size:.12,transparent:true,opacity:.42,depthWrite:false}));scene.add(starfield);
}
function initialize(){
  if(renderer)return true;if(failed)return false;
  try{
    renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,1.75));
    renderer.outputColorSpace=THREE.SRGBColorSpace;
    host.insertBefore(renderer.domElement,tooltip);
    scene=new THREE.Scene();scene.fog=new THREE.FogExp2(0x091521,.012);
    camera=new THREE.PerspectiveCamera(56,1,.1,180);
    group=new THREE.Group();scene.add(group);
    geometry=new THREE.SphereGeometry(1,14,10);makeStars();
    observer=new ResizeObserver(resize);observer.observe(host);resize();
    renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();failed=true;setActive(false);window.atlas3DFailed?.('3D 그래픽 연결이 끊겨 2D 지도로 전환했습니다.');});
    return true;
  }catch(error){failed=true;renderer?.dispose();renderer=undefined;window.atlas3DFailed?.('이 브라우저에서는 WebGL 3D를 사용할 수 없어 2D 지도로 전환했습니다.');return false;}
}
function resize(){if(!renderer)return;const w=Math.max(1,host.clientWidth),h=Math.max(1,host.clientHeight);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();drawFrame();}
function drawFrame(){if(!renderer||!active)return;focus.lerp(desired,.16);camera.position.set(focus.x+Math.sin(az)*Math.cos(el)*distance,focus.y+Math.sin(el)*distance,focus.z+Math.cos(az)*Math.cos(el)*distance);camera.lookAt(focus);renderer.render(scene,camera);}
function animate(){if(!active)return;drawFrame();frame=requestAnimationFrame(animate);}
function setActive(value){active=Boolean(value);if(active&&!initialize())return false;cancelAnimationFrame(frame);if(active){resize();animate();}else if(renderer)renderer.clear();return true;}
function disposeGraph(){for(const m of nodeMeshes)m.material.dispose();for(const e of edgeMeshes){e.geometry.dispose();e.material.dispose();}for(const l of labels){l.material.map.dispose();l.material.dispose();}group?.clear();nodeMeshes=[];edgeMeshes=[];labels=[];}
function communityColors(data){
  const parent=new Map(data.nodes.map(n=>[n.id,n.id]));
  const find=id=>{let x=id;while(parent.get(x)!==x)x=parent.get(x);return x;};
  for(const e of data.edges)if(e.layer==='semantic'&&parent.has(e.source)&&parent.has(e.target))parent.set(find(e.target),find(e.source));
  const groups=new Map();return new Map(data.nodes.map(n=>{const root=find(n.id);if(!groups.has(root))groups.set(root,groups.size);return [n.id,groups.get(root)];}));
}
function positions(data){
  const map=new Map(),n=Math.max(1,data.nodes.length);
  data.nodes.forEach((node,i)=>{
    const seed=hash(node.id),angle=i*2.39996323+(seed%97)/320;
    const radius=2+Math.sqrt((i+.5)/n)*12;
    const tier={topic:4,entity:2,concept:1,event:0,issue:-1,question:3,claim:-2,source:-5}[node.type]||0;
    map.set(node.id,new THREE.Vector3(Math.cos(angle)*radius,Math.sin(angle)*radius*.8+tier*.42,tier+(seed%1200/1200-.5)*5));
  });
  // Short deterministic relaxation makes linked nodes discoverable without inventing a relation.
  for(let step=0;step<18;step++)for(const edge of data.edges){const a=map.get(edge.source),b=map.get(edge.target);if(!a||!b)continue;const delta=b.clone().sub(a),length=delta.length()||1;const amount=clamp((length-5)*.012,-.12,.12);delta.multiplyScalar(amount/length);a.add(delta);b.sub(delta);}
  return map;
}
function labelSprite(text,color){
  const canvas=document.createElement('canvas');canvas.width=512;canvas.height=76;const ctx=canvas.getContext('2d');
  ctx.fillStyle='rgba(7,22,37,.85)';ctx.beginPath();ctx.roundRect(2,3,508,70,12);ctx.fill();ctx.strokeStyle=color;ctx.lineWidth=3;ctx.stroke();
  ctx.font='bold 28px system-ui, sans-serif';ctx.fillStyle='#f4f9fc';ctx.textBaseline='middle';ctx.fillText(text.length>21?text.slice(0,20)+'…':text,18,39,478);
  const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
  const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthWrite:false}));sprite.scale.set(5.8,.86,1);return sprite;
}
function render(data,selection,colorMode){
  current=data;selected=selection||'';if(!renderer&&!active)return;if(!initialize())return;
  disposeGraph();const coords=positions(data),community=communityColors(data);
  const degrees=new Map(data.nodes.map(n=>[n.id,0]));for(const e of data.edges){degrees.set(e.source,(degrees.get(e.source)||0)+1);degrees.set(e.target,(degrees.get(e.target)||0)+1);}
  const labelIds=new Set([...data.nodes].filter(n=>n.type!=='source').sort((a,b)=>(degrees.get(b.id)||0)-(degrees.get(a.id)||0)).slice(0,12).map(n=>n.id));
  for(const edge of data.edges){const a=coords.get(edge.source),b=coords.get(edge.target);if(!a||!b)continue;
    const geo=new THREE.BufferGeometry().setFromPoints([a,b]);const material=new THREE.LineBasicMaterial({color:edgeColors[edge.layer]||0x7196a5,transparent:true,opacity:edge.layer==='semantic'?.58:edge.layer==='recommendation'?.24:.28,depthWrite:false});
    const line=new THREE.Line(geo,material);line.userData={layer:edge.layer,source:edge.source,target:edge.target};group.add(line);edgeMeshes.push(line);
  }
  for(const node of data.nodes){
    const color=colorMode==='community'?palette[(community.get(node.id)||0)%palette.length]:palette[Math.max(0,types.indexOf(node.type))];
    const material=new THREE.MeshBasicMaterial({color,transparent:true,opacity:node.id===selected?1:.9});
    const mesh=new THREE.Mesh(geometry,material);mesh.position.copy(coords.get(node.id));const size=clamp(.24+Math.sqrt(degrees.get(node.id)||0)*.11,.25,.82);mesh.scale.setScalar(node.id===selected?size*1.5:size);mesh.userData={id:node.id,title:node.title,size};group.add(mesh);nodeMeshes.push(mesh);
    if(node.id===selected||labelIds.has(node.id)){
      const label=labelSprite(node.title,color);label.position.copy(mesh.position).add(new THREE.Vector3(0,size+0.72,0));group.add(label);labels.push(label);
    }
  }
  const focusChanged=Boolean(selected&&selected!==lastSelected&&coords.has(selected)&&!preserveFocusOnce);
  if(focusChanged)desired.copy(coords.get(selected));
  else if(!selected&&lastSelected)desired.set(0,0,0);
  preserveFocusOnce=false;lastSelected=selected;highlight();drawFrame();if(focusChanged)window.atlas3DPoseChanged?.();
}
function highlight(){
  const linked=new Set([selected||hovered]);const id=selected||hovered;
  for(const e of current?.edges||[])if(e.source===id||e.target===id){linked.add(e.source);linked.add(e.target);}
  for(const mesh of nodeMeshes){mesh.material.opacity=!id||linked.has(mesh.userData.id)?1:.24;mesh.scale.setScalar(mesh.userData.size*(mesh.userData.id===selected?1.5:1));}
  for(const edge of edgeMeshes)edge.material.opacity=id&&(edge.userData.source===id||edge.userData.target===id)?.88:id?.07:edge.userData.layer==='semantic'?.58:edge.userData.layer==='recommendation'?.24:.28;
}
function pick(event){if(!renderer||!active)return null;const rect=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera);return raycaster.intersectObjects(nodeMeshes,false)[0]?.object||null;}
function showHover(mesh){hovered=mesh?.userData.id||'';tooltip.style.display=mesh?'block':'none';tooltip.textContent=mesh?.userData.title||'';highlight();}
function reset(){az=.18;el=.28;distance=26;desired.set(0,0,0);window.atlas3DPoseChanged?.();drawFrame();}
function pose(){return {az:round(az),el:round(el),dist:round(distance),tx:round(desired.x),ty:round(desired.y),tz:round(desired.z)};}
function restore(p){for(const [key,min,max] of [['az',-100,100],['el',-1.42,1.42],['dist',7,90]])if(Number.isFinite(Number(p[key])))({az:v=>az=v,el:v=>el=v,dist:v=>distance=v}[key])(clamp(Number(p[key]),min,max));preserveFocusOnce=['tx','ty','tz'].some(key=>p[key]!==undefined);for(const [key,axis] of [['tx','x'],['ty','y'],['tz','z']])if(Number.isFinite(Number(p[key])))desired[axis]=clamp(Number(p[key]),-40,40);focus.copy(desired);drawFrame();}
function zoom(delta){distance=clamp(distance*Math.exp(delta*.001),7,90);drawFrame();window.atlas3DPoseChanged?.();}
host.addEventListener('pointerdown',event=>{if(!active)return;fingers.set(event.pointerId,{x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY});dragged=false;host.setPointerCapture(event.pointerId);});
host.addEventListener('pointermove',event=>{
  if(!active)return;const old=fingers.get(event.pointerId);
  if(!old){const hit=pick(event);if(hit?.userData.id!==hovered)showHover(hit);return;}
  const dx=event.clientX-old.x,dy=event.clientY-old.y;dragged ||= Math.hypot(event.clientX-old.startX,event.clientY-old.startY)>4;
  if(fingers.size===2){const other=[...fingers.entries()].find(([id])=>id!==event.pointerId)?.[1];if(other){const before=Math.hypot(old.x-other.x,old.y-other.y),after=Math.hypot(event.clientX-other.x,event.clientY-other.y);if(before&&after)distance=clamp(distance*before/after,7,90);}}
  else{az-=dx*.007;el=clamp(el-dy*.007,-1.42,1.42);}old.x=event.clientX;old.y=event.clientY;showHover(null);drawFrame();
});
function endPointer(event){const was=fingers.get(event.pointerId);fingers.delete(event.pointerId);if(was&&!dragged){const hit=pick(event);if(hit)window.atlas3DSelect?.(hit.userData.id);}window.atlas3DPoseChanged?.();}
host.addEventListener('pointerup',endPointer);host.addEventListener('pointercancel',event=>{fingers.delete(event.pointerId);window.atlas3DPoseChanged?.();});
host.addEventListener('pointerleave',()=>{if(!fingers.size)showHover(null);});
host.addEventListener('wheel',event=>{if(!active)return;event.preventDefault();zoom(event.deltaY);},{passive:false});
host.addEventListener('keydown',event=>{let used=true;if(event.key==='ArrowLeft')az-=.15;else if(event.key==='ArrowRight')az+=.15;else if(event.key==='ArrowUp')el=clamp(el+.12,-1.42,1.42);else if(event.key==='ArrowDown')el=clamp(el-.12,-1.42,1.42);else if(event.key==='+'||event.key==='=')distance=clamp(distance*.84,7,90);else if(event.key==='-')distance=clamp(distance*1.18,7,90);else if(event.key==='Home')reset();else used=false;if(used){event.preventDefault();drawFrame();window.atlas3DPoseChanged?.();}});
window.atlas3D={setActive,render,reset,pose,restore,get ready(){return Boolean(renderer)&&!failed;}};
window.atlas3DReady?.();
