import {QualityController} from './logic.mjs';
import {previewFrame,PreviewClock} from './preview-input.mjs';
import {PlanetRenderer} from './planet-renderer.mjs';
const query=new URLSearchParams(location.search),$=id=>document.getElementById(id);
let renderer,quality,frameId=0,scene=query.get('scene')||'planet',view=query.get('view')||'oblique',paused=query.has('still'),base=performance.now(),time=Number(query.get('t')||8),samples=[],intervals=[],last=null,report=0;
const clock=new PreviewClock();let simulated=false,gazeMode='off',handMode='none',mouse=[innerWidth/2,innerHeight/2],snapshotControl=null;
const initialTime=Number.isFinite(time)&&time>=0?time:8;time=initialTime;
const clean=query.has('clean'),debug=query.has('debug');
document.body.classList.add('preview');document.title='粒子行星 · 无摄像头预览';$('fallback').hidden=true;
const panel=document.createElement('section');panel.id='preview-controls';
panel.innerHTML='<div class="preview-caption">无摄像头预览 · 以下交互均为合成输入</div><div class="preview-buttons"><button data-scene="planet">粒子行星</button><button data-scene="galaxy">螺旋星系</button><button data-scene="text">文字</button><select id="preview-view" aria-label="镜头"><option value="oblique">斜视</option><option value="top">俯视</option><option value="face">银河正面</option><option value="close">近景</option></select><button id="preview-pause">暂停</button><button id="preview-replay">重播开场</button><button id="preview-full">全屏</button></div><div class="preview-buttons preview-simulation"><span>模拟：</span><button data-gaze="off">关闭视线</button><button data-gaze="center">凝视中心（推近）</button><button data-gaze="mouse">鼠标模拟视线</button><button data-hand="open">张手 O</button><button data-hand="fist">握拳 F</button><button data-hand="none">无手 N</button><button id="preview-burst">打散 B</button><button id="preview-reset">恢复静止</button></div><output id="preview-stats"></output>';
document.body.append(panel);panel.hidden=clean;$('preview-view').value=view;
for(const control of panel.querySelectorAll('button,select'))control.disabled=true;
$('preview-pause').textContent=paused?'继续':'暂停';
let options;
function activateSimulation(){simulated=true;snapshotControl=null;paused=false;base=performance.now()-time*1000;redraw();}
// Deterministic inspection uses the live dynamics without submitting hundreds of GPU frames.
// All four states use t=8 and the same camera angles; only zoom changes distance.
function snapshot(next,state,t=8,gazeUV=null){
 if(!['base','scatter','well','zoom','restore','view'].includes(state))throw new Error('Unknown preview state');
 cancelAnimationFrame(frameId);paused=true;simulated=false;scene=next;view='oblique';time=t;
 renderer.setScene(scene);renderer.resetControls();renderer.placeCamera(view);
 let gaze=gazeUV?[gazeUV[0]*innerWidth,gazeUV[1]*innerHeight]:null;
 if(state==='zoom')gaze=[innerWidth/2,innerHeight/2];
 if(state==='well'){
  let point=scene==='planet'?[3,0,0]:[2,0,0];
  if(scene==='text'){const p=renderer.items[0].geometry.attributes.position.array;let best=Infinity;
   for(let n=0;n<p.length;n+=3){const d=(p[n]+2)**2+p[n+1]**2;if(d<best){best=d;point=[p[n],p[n+1],0];}}}
  const projected=renderer.depthPoint.set(...point).project(renderer.camera);
  gaze=[(projected.x+1)*innerWidth/2,(1-projected.y)*innerHeight/2];
 }
 let control;const pattern=['planet','galaxy','text'].indexOf(scene);
 for(let n=0;n<=70;n++)control=renderer.updateControls(previewFrame(n*50000,[innerWidth,innerHeight],pattern,gaze,state==='scatter'||state==='restore'?'open':'none'),.05,1,pattern);
 if(state==='restore')for(let n=71;n<=250;n++)control=renderer.updateControls(previewFrame(n*50000,[innerWidth,innerHeight],pattern,null,'fist'),.05,1,pattern);
 snapshotControl={...control,yaw:gazeUV?control.yaw:0,pitch:gazeUV?control.pitch:0,zoom:state==='zoom'?control.zoom:0};
 renderer.placeCamera(view,snapshotControl);
 renderer.applyWell(previewFrame(0,[innerWidth,innerHeight],pattern,gaze),snapshotControl,.05);
 redraw();
 return renderInfo(state,gaze);
}
function renderInfo(state,gaze=null){
 const u=renderer.items.at(-1).material.uniforms,projection=[];
 if(scene==='text'){
  const b=renderer.textBounds,depth=renderer.o.text_depth/2+renderer.o.text_flow;
  for(const x of [b[0],b[1]])for(const y of [b[2],b[3]])for(const z of [-depth,depth]){
   const p=renderer.depthPoint.set(x,y,z).multiplyScalar(renderer.modelScale).project(renderer.camera);projection.push([p.x,p.y]);}
 }
 return {scene,state,time,particles:renderer.count,zoom:snapshotControl?.zoom??0,camera_distance:renderer.camera.position.length(),pose:renderer.pose,
  scatter:u.scatter.value,well_alpha:u.eyeAlpha.value,well_radius:u.wellRadius.value,point_max:u.pointMax.value,letter_selected:renderer.letterFocus?.selected??-1,letter_weights:[...(renderer.letterFocus?.weights??[])].slice(0,(renderer.letterFocus?.letters.at(-1)?.id??-1)+1),transition:renderer.transitionInfo,bloom:renderer.bloom.strength,
  text_projection:projection,hit:gaze?renderer.pick(gaze).hit:false};
}
function inspectView(next,uv,zoom=0){
 snapshot(next,'view',8,uv);snapshotControl.zoom=zoom;redraw();return renderInfo('edge-view');
}
function sequenceSample(next,ms){
 cancelAnimationFrame(frameId);paused=true;simulated=false;scene=next;view='oblique';time=8+ms/1000;
 renderer.setScene(scene);renderer.resetControls();renderer.placeCamera(view);
 const pattern=['planet','galaxy','text'].indexOf(scene);
 snapshotControl=null;
 for(let t=0;t<=ms;t+=20)snapshotControl=renderer.updateControls(previewFrame(t*1000,[innerWidth,innerHeight],pattern,null,t===0?'none':t<650?'open':'fist'),t===0?0:.02,1,pattern);
 redraw();return renderInfo('scatter-return-sequence');
}


function letterSample(id=0){
 cancelAnimationFrame(frameId);paused=true;simulated=false;scene='text';view='oblique';time=8;
 renderer.setScene(scene);renderer.resetControls();renderer.placeCamera(view);
 const letter=renderer.letterFocus.letters.find(l=>l.id===id);if(!letter)throw new Error('字母编号无效');
 const p=renderer.depthPoint.set(...letter.center,0).project(renderer.camera),xy=[(p.x+1)*innerWidth/2,(1-p.y)*innerHeight/2];
 for(let n=0;n<=20;n++)snapshotControl=renderer.updateControls(previewFrame(n*50000,[innerWidth,innerHeight],2,xy),.05,1,2);
 snapshotControl={...snapshotControl,yaw:0,pitch:0,zoom:0};redraw();return renderInfo('letter-focus',xy);
}
function assemblySample(from,to,ms){
 cancelAnimationFrame(frameId);paused=true;simulated=false;scene=to;view='oblique';time=8+ms/1000;
 renderer.setScene(from);renderer.resetControls();renderer.placeCamera(view);const target=['planet','galaxy','text'].indexOf(to),duration=(options.transition_scatter_s+options.transition_s)*1000;
 for(let t=0;t<=ms;t+=20)snapshotControl=renderer.updateControls(previewFrame(t*1000,[innerWidth,innerHeight],target,[0,0],t<duration?'open':'none'),t===0?0:.02,1,target);
 redraw();return renderInfo('assembly-sequence');
}
function eyeBurstSample(next,ms,status){
 cancelAnimationFrame(frameId);paused=true;simulated=false;scene=next;view='oblique';time=8+ms/1000;
 renderer.setScene(next);renderer.resetControls();renderer.placeCamera(view);renderer.acceptBurst(status,ms);
 snapshotControl=renderer.updateControls(previewFrame(ms*1000,[innerWidth,innerHeight],['planet','galaxy','text'].indexOf(next),null),0,1,['planet','galaxy','text'].indexOf(next),'oblique',ms);
 redraw();return {...renderInfo('eye-burst'),...status};
}


function combinedSample(next,elapsed){
 snapshot(next,'scatter',8);renderer.burst.trigger(renderer.clockMs-elapsed);
 snapshotControl=renderer.updateControls(previewFrame(0,[innerWidth,innerHeight],['planet','galaxy','text'].indexOf(next),null,'open'),0,1,['planet','galaxy','text'].indexOf(next));
 redraw();return {...renderInfo('combined-scatter'),hand_scatter:renderer.hands.state.scatter,burst_scatter:renderer.burst.value(renderer.clockMs)};
}

function draw(now){
 const dt=last===null?null:now-last;last=now;
 if(!paused)time=(now-base)/1000;
 if(!paused&&!document.hidden){if(quality.frame(now))renderer.resize();}else quality.frame(now,false);
 const intro=paused?1:Math.min(1,time/options.intro_s),began=performance.now();
 let control=snapshotControl;
 if(simulated){const gaze=gazeMode==='center'?[innerWidth/2,innerHeight/2]:gazeMode==='mouse'?mouse:null;
  const frame=clock.sample(now,[innerWidth,innerHeight],['planet','galaxy','text'].indexOf(scene),gaze,handMode);
  control=renderer.updateControls(frame,Math.min(.1,(dt??16.67)/1000),intro,frame.pattern,view);}
 renderer.render(time,intro,view,control);
 samples.push(performance.now()-began);if(dt!==null)intervals.push(dt);
 if(now-report>=1000){
  const sorted=[...samples].sort((a,b)=>a-b),median=sorted[Math.floor(sorted.length/2)],p90=sorted[Math.floor((sorted.length-1)*.9)];
  const fps=intervals.length?1000*intervals.length/intervals.reduce((a,b)=>a+b,0):null;
  window.__galaxyPreview.metrics={submit_median_ms:median,submit_p90_ms:p90,fps,frames:intervals.length};
  $('preview-stats').textContent=renderer.count.toLocaleString()+' 粒子 · '+(paused?'静帧':debug?'FPS '+(fps?.toFixed(1)??'—')+' · 提交 '+median.toFixed(2)+' / P90 '+p90.toFixed(2)+' ms · DPR '+renderer.pixelRatio.toFixed(2)+' · 降档 '+quality.downgrades:'持续动画');
  samples=[];intervals=[];report=now;
 }
 if(!paused)frameId=requestAnimationFrame(draw);
}
function redraw(){cancelAnimationFrame(frameId);last=null;draw(performance.now());}
function chooseScene(next,immediate=false){
 if(!immediate&&renderer.transitionInfo?.busy)return;
 scene=next;
 if(immediate){renderer.setScene(scene);renderer.resetControls();}else{renderer.assembly.request(['planet','galaxy','text'].indexOf(scene),renderer.clockMs);simulated=true;paused=false;base=performance.now()-time*1000;}
 clock.reset();snapshotControl=null;for(const b of panel.querySelectorAll('[data-scene]'))b.classList.toggle('selected',b.dataset.scene===scene);redraw();}
async function init(){
 try{
  $('message').textContent='正在准备本地粒子画面……';
  const response=await fetch('/preview-config',{cache:'no-store'});
  if(!response.ok)throw new Error('请先运行 python -m tools.preview_galaxy');
  options=await response.json();quality=new QualityController(options,options.visual.planet_particles+options.visual.ring_particles,options.visual.low_particle_count);renderer=new PlanetRenderer($('universe'),options,quality);renderer.setScene(scene);
  window.__galaxyPreview={ready:true,get scene(){return scene;},get time(){return time;},get view(){return view;},metrics:null,snapshot,inspectView,sequenceSample,combinedSample,letterSample,assemblySample,eyeBurstSample,async spotlightCheck(){const {checkSpotlights}=await import('./galaxy-gpu-check.mjs');try{return checkSpotlights(renderer);}finally{redraw();}},async galaxyCheck(times){const {checkGalaxyGPU}=await import('./galaxy-gpu-check.mjs');try{return checkGalaxyGPU(renderer,times);}finally{redraw();}},get simulation(){return {enabled:simulated,gaze:gazeMode,hand:handMode,capture_us:clock.frame?.capture_us,scatter:renderer.hands?.state.scatter??0,camera_distance:renderer.camera.position.length()};},
   freeze(t){paused=true;time=t;redraw();},setScene(next){chooseScene(next,true);},setView(next){view=next;$('preview-view').value=next;redraw();}};
  $('gate').hidden=true;for(const control of panel.querySelectorAll('button,select'))control.disabled=false;
  base=performance.now();chooseScene(scene,true);
 }catch(error){$('gate').hidden=false;$('message').textContent='画面预览不可用：'+error.message;panel.hidden=true;}
}
panel.addEventListener('click',async event=>{
 const target=event.target;
 if(target.id==='preview-burst'&&!renderer.transitionInfo?.busy){renderer.burst.trigger(renderer.clockMs);activateSimulation();return;}
 if(target.dataset.gaze){gazeMode=target.dataset.gaze;activateSimulation();return;}
 if(target.dataset.hand){handMode=target.dataset.hand;activateSimulation();return;}
 if(target.id==='preview-reset'){simulated=false;snapshotControl=null;gazeMode='off';handMode='none';renderer.resetControls();clock.reset();redraw();return;}
 if(target.dataset.scene){chooseScene(target.dataset.scene);return;}
 if(target.id==='preview-pause'){paused=!paused;if(!paused)base=performance.now()-time*1000;target.textContent=paused?'继续':'暂停';redraw();}
 if(target.id==='preview-replay'){time=0;base=performance.now();paused=false;$('preview-pause').textContent='暂停';redraw();}
 if(target.id==='preview-full'){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('preview-stats').textContent='请用浏览器菜单进入全屏';}}
});
$('universe').addEventListener('pointermove',event=>{mouse=[event.clientX,event.clientY];});
addEventListener('keydown',event=>{
 if(!renderer||event.target.matches?.('input,select,textarea')||event.repeat)return;
 if(event.key.toLowerCase()==='b'&&!renderer.transitionInfo?.busy){renderer.burst.trigger(renderer.clockMs);activateSimulation();return;}
 const hand={o:'open',f:'fist',n:'none'}[event.key.toLowerCase()];
 if(hand){handMode=hand;activateSimulation();}
 if(event.key.toLowerCase()==='g'){gazeMode=gazeMode==='mouse'?'off':'mouse';activateSimulation();}
});
$('preview-view').addEventListener('change',event=>{view=event.target.value;redraw();});
addEventListener('resize',()=>{renderer?.resize();if(renderer)redraw();});
document.addEventListener('visibilitychange',()=>{quality?.frame(performance.now(),false);last=null;if(!document.hidden&&renderer)redraw();});
addEventListener('pagehide',()=>{cancelAnimationFrame(frameId);renderer?.dispose();});
if(debug)$('preview-stats').classList.add('diagnostic');
init();
