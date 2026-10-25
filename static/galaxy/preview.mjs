import {PlanetRenderer} from './planet-renderer.mjs';
const query=new URLSearchParams(location.search),$=id=>document.getElementById(id);
let renderer,frameId=0,scene=query.get('scene')||'planet',view=query.get('view')||'oblique',paused=query.has('still'),base=performance.now(),time=Number(query.get('t')||8),samples=[],intervals=[],last=null,report=0;
const initialTime=Number.isFinite(time)&&time>=0?time:8;time=initialTime;
const clean=query.has('clean'),debug=query.has('debug');
document.body.classList.add('preview');document.title='粒子行星 · 无摄像头预览';$('fallback').hidden=true;
const panel=document.createElement('section');panel.id='preview-controls';
panel.innerHTML='<div class="preview-caption">画面预览 · 不使用摄像头、眼动或手势</div><div class="preview-buttons"><button data-scene="planet">粒子行星</button><button data-scene="galaxy">螺旋星系</button><button data-scene="text">文字</button><select id="preview-view" aria-label="镜头"><option value="oblique">斜视</option><option value="top">俯视</option><option value="close">近景</option></select><button id="preview-pause">暂停</button><button id="preview-replay">重播开场</button><button id="preview-full">全屏</button></div><output id="preview-stats"></output>';
document.body.append(panel);panel.hidden=clean;$('preview-view').value=view;
for(const control of panel.querySelectorAll('button,select'))control.disabled=true;
$('preview-pause').textContent=paused?'继续':'暂停';
let options;
function draw(now){
 const dt=last===null?null:now-last;last=now;
 if(!paused)time=(now-base)/1000;
 const intro=paused?1:Math.min(1,time/options.intro_s),began=performance.now();renderer.render(time,intro,view);
 samples.push(performance.now()-began);if(dt!==null)intervals.push(dt);
 if(now-report>=1000){
  const sorted=[...samples].sort((a,b)=>a-b),median=sorted[Math.floor(sorted.length/2)],p90=sorted[Math.floor((sorted.length-1)*.9)];
  const fps=intervals.length?1000*intervals.length/intervals.reduce((a,b)=>a+b,0):null;
  window.__galaxyPreview.metrics={submit_median_ms:median,submit_p90_ms:p90,fps,frames:intervals.length};
  $('preview-stats').textContent=(options.visual.planet_particles+options.visual.ring_particles).toLocaleString()+' 粒子 · '+(paused?'静帧':debug?'FPS '+(fps?.toFixed(1)??'—')+' · 提交 '+median.toFixed(2)+' / P90 '+p90.toFixed(2)+' ms':'持续动画');
  samples=[];intervals=[];report=now;
 }
 if(!paused)frameId=requestAnimationFrame(draw);
}
function redraw(){cancelAnimationFrame(frameId);last=null;draw(performance.now());}
function chooseScene(next){scene=next;renderer.setScene(scene);for(const b of panel.querySelectorAll('[data-scene]'))b.classList.toggle('selected',b.dataset.scene===scene);redraw();}
async function init(){
 try{
  $('message').textContent='正在准备本地粒子画面……';
  const response=await fetch('/preview-config',{cache:'no-store'});
  if(!response.ok)throw new Error('请先运行 python -m tools.preview_galaxy');
  options=await response.json();renderer=new PlanetRenderer($('universe'),options);renderer.setScene(scene);
  window.__galaxyPreview={ready:true,get scene(){return scene;},get time(){return time;},get view(){return view;},metrics:null,
   freeze(t){paused=true;time=t;redraw();},setScene:chooseScene,setView(next){view=next;$('preview-view').value=next;redraw();}};
  $('gate').hidden=true;for(const control of panel.querySelectorAll('button,select'))control.disabled=false;
  base=performance.now();chooseScene(scene);
 }catch(error){$('gate').hidden=false;$('message').textContent='画面预览不可用：'+error.message;panel.hidden=true;}
}
panel.addEventListener('click',async event=>{
 const target=event.target;if(target.dataset.scene){chooseScene(target.dataset.scene);return;}
 if(target.id==='preview-pause'){paused=!paused;if(!paused)base=performance.now()-time*1000;target.textContent=paused?'继续':'暂停';redraw();}
 if(target.id==='preview-replay'){time=0;base=performance.now();paused=false;$('preview-pause').textContent='暂停';redraw();}
 if(target.id==='preview-full'){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{$('preview-stats').textContent='请用浏览器菜单进入全屏';}}
});
$('preview-view').addEventListener('change',event=>{view=event.target.value;redraw();});
addEventListener('resize',()=>{renderer?.resize();if(renderer)redraw();});
document.addEventListener('visibilitychange',()=>{last=null;if(!document.hidden&&renderer)redraw();});
addEventListener('pagehide',()=>{cancelAnimationFrame(frameId);renderer?.dispose();});
if(debug)$('preview-stats').classList.add('diagnostic');
init();
