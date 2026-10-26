import {Intro,QualityController,usableFrame} from './logic.mjs';
import {SceneSelection,sceneDirection,cornerDiagnostic} from './scene-controls.mjs';
import {CornerIndicator} from './corner-indicator.mjs';
import {handDiagnostic} from './planet-controls.mjs';
const $=id=>document.getElementById(id),debug=new URLSearchParams(location.search).has('debug');
const token=location.hash.slice(1);history.replaceState(null,'',location.pathname+location.search);
const scene=new SceneSelection();let indicator,cornerStatus=null,debugBase='';
let socket,renderer,options,quality,intro,latest=null,received=0,revision=0,nativeFull=false,ready=false,raf=0,last=null,reportAt=0,intervals=[],renderTimes=[],longFrames=0;
const send=message=>{if(socket?.readyState===1)socket.send(JSON.stringify(message));};
function updateDebug(){if(debug)$('debug').textContent=debugBase+'\n'+cornerDiagnostic(latest,cornerStatus,[innerWidth,innerHeight]);}
function acceptScene(status){if(ready&&status?.viewport===revision&&scene.accept(status)){cornerStatus=status;updateDebug();}}
function message(text){$('message').textContent=text;}
function fail(text,code=1){ready=false;indicator?.draw(null);cancelAnimationFrame(raf);$('gate').hidden=false;message(text);send({type:'error',code});}
function invalidate(){if(!ready)return;ready=false;latest=null;cornerStatus=null;indicator?.draw(null);$('gate').hidden=false;message('窗口或全屏状态已变化，请在内置屏重新点击进入。');send({type:'invalidate'});}
async function start(event){
 if(!options||!renderer)return;
 if(!document.fullscreenElement && !nativeFull){
  try{await document.documentElement.requestFullscreen();message('已进入全屏，请再次点击以核对实际视口位置。');}
  catch{message('请先将浏览器移到内置屏并进入全屏，再点击进入。');}return;
 }
 const scale=window.visualViewport?.scale??1;
 send({type:'geometry',screenX:window.screenX,screenY:window.screenY,innerWidth,innerHeight,outerWidth,outerHeight,
  dpr:devicePixelRatio,screenWidth:screen.width,screenHeight:screen.height,fullscreen:1,
  clientX:event.clientX,clientY:event.clientY,eventScreenX:event.screenX,eventScreenY:event.screenY,scale});
}
function tick(now){
 if(!ready)return;
 const elapsed=last===null?0:(now-last)/1000,dt=Math.min(.05,elapsed);last=now;
 if(!document.hidden){
  // rAF now may precede this callback/WS receive; use actual receipt-clock age.
  const packetAge=Math.max(0,performance.now()-received);
  const usable=usableFrame(latest,revision,packetAge,options.stale_ms),frame=usable?latest:null,progress=intro.progress(now);
  const renderStart=performance.now();
  try{
  if(quality.frame(now)){renderer.resize();}
  renderer.draw(frame,dt,now/1000,progress,scene.pattern);renderTimes.push(performance.now()-renderStart);
  }catch(error){fail('星系绘制失败：'+error.message+'。可切回 Qt 星空。',2);return;}
  indicator.draw(cornerStatus,usable&&progress>=1);
  $('eye').classList.toggle('online',!!frame?.gaze_valid);$('hand').classList.toggle('online',!!(frame?.control&&frame?.hands.length));
  $('eye').title=frame?.gaze_valid?'视线在线':'视线离线';$('hand').title=handDiagnostic(latest,usable);
  $('hand').classList.toggle('warming',!!frame?.hand_detected&&!frame?.control);
  if(frame?.gaze_valid&&debug){$('gaze').hidden=false;$('gaze').style.left=frame.xy[0]+'px';$('gaze').style.top=frame.xy[1]+'px';}else $('gaze').hidden=true;
  if(elapsed>0){intervals.push(elapsed*1000);if(elapsed*1000>1000/50)longFrames++;}
  if(now-reportAt>=1000&&intervals.length){
   const sorted=[...intervals].sort((a,b)=>a-b),fps=1000*intervals.length/intervals.reduce((a,b)=>a+b,0);
   const p90=sorted[Math.floor((sorted.length-1)*.9)],cost=[...renderTimes].sort((a,b)=>a-b);
   const renderMedian=cost[Math.floor((cost.length-1)*.5)],renderP90=cost[Math.floor((cost.length-1)*.9)];
   send({type:'performance',fps,p90_ms:p90,frames:intervals.length,long_frames:longFrames,particles:quality.particles,pixel_ratio:Math.min(devicePixelRatio,quality.ratio),downgrades:quality.downgrades,render_median_ms:renderMedian,render_p90_ms:renderP90});
   if(debug)debugBase='FPS '+fps.toFixed(1)+'  P90 '+p90.toFixed(1)+' ms\n粒子 '+quality.particles+'  像素比 '+quality.ratio.toFixed(2)+'  降档 '+quality.downgrades+'\n渲染提交 '+renderMedian.toFixed(2)+' / P90 '+renderP90.toFixed(2)+' ms\n视口 '+innerWidth+' × '+innerHeight+'  版本 '+revision+'\n'+(progress<1?'开场汇聚':'眼手交互')+' · '+renderer.sceneName+'\n'+handDiagnostic(latest,usable)+'\n手势检测 '+(latest?.hand_ms?.toFixed(1)??'—')+' ms · 处理+接收后 '+(latest?((latest.completed_us-latest.capture_us)/1000+packetAge).toFixed(1):'—')+' ms';
   updateDebug();reportAt=now;intervals=[];renderTimes=[];longFrames=0;
  }
 }else{quality.frame(now,false);last=null;}
 raf=requestAnimationFrame(tick);
}
async function connect(){
 if(!token){fail('会话令牌缺失，请从悬浮层重新启动。');return;}
 socket=new WebSocket('ws://'+location.host+'/ws');
 socket.addEventListener('open',()=>{send({type:'auth',token});send({type:'focus',active:Number(document.hasFocus())});});
 socket.addEventListener('message',async event=>{
  try{
   const m=JSON.parse(event.data);
   if(m.type==='config'){
    options=m.options;quality=new QualityController(options,options.visual.planet_particles+options.visual.ring_particles,options.visual.low_particle_count);intro=new Intro(options.intro_s);
    const {GalaxyRenderer}=await import('./renderer.mjs');renderer=new GalaxyRenderer($('universe'),options,quality);indicator=new CornerIndicator(options.interaction);
    $('start').disabled=false;message('窗口就绪后点击进入。目光改变观察角度；凝视行星推近，凝视角落切换图案。');
   }else if(m.type==='host'){nativeFull=m.fullscreen===1;}
   else if(m.type==='ready'){
    cancelAnimationFrame(raf);revision=m.viewport;ready=true;latest=null;last=null;intervals=[];renderTimes=[];longFrames=0;
    scene.reset();cornerStatus=null;indicator.draw(null);intro.begin(performance.now());$('gate').hidden=true;renderer.reset();renderer.resize();raf=requestAnimationFrame(tick);
   }else if(m.type==='frame'){latest=m.data;received=performance.now();acceptScene(m.data);updateDebug();}
   else if(m.type==='scene'){acceptScene(m.data);}
   else if(m.type==='geometry_error'){message('视口核对未通过。请移到内置屏、保持100%缩放并进入全屏，再点击进入。');}
  }catch(error){fail('星系渲染不可用：'+error.message+'。可切回 Qt 星空。',2);}
 });
 socket.addEventListener('close',()=>{if(!ended)fail('本地连接已断开，请返回悬浮层重试或切回 Qt 星空。',3);});
 socket.addEventListener('error',()=>message('无法连接本机，请在悬浮层选择 Qt 星空备用。'));
}
let ended=false;
function stop(){ended=true;send({type:'stop'});cancelAnimationFrame(raf);renderer?.close();indicator?.dispose();socket?.close();window.close();$('gate').hidden=false;message('体验已结束，可关闭此页面。');}
$('start').addEventListener('click',start);$('fallback').addEventListener('click',()=>{send({type:'fallback'});ended=true;window.close();});
addEventListener('keydown',event=>{
 if(event.key==='Escape'){event.preventDefault();stop();return;}
 const direction=sceneDirection(event);
 if(direction&&ready&&intro.interactive(performance.now())){event.preventDefault();send({type:'scene_step',direction,viewport:revision});}
});
addEventListener('resize',()=>{renderer?.resize();invalidate();});
function watchDpr(){const query=matchMedia('(resolution: '+devicePixelRatio+'dppx)');query.addEventListener('change',()=>{renderer?.resize();invalidate();watchDpr();},{once:true});}watchDpr();
document.addEventListener('visibilitychange',()=>{quality?.frame(performance.now(),false);last=null;intervals=[];renderTimes=[];longFrames=0;});
$('universe').addEventListener('webglcontextlost',event=>{event.preventDefault();if(!ended)fail('GPU连接中断，可切回 Qt 星空。',2);});
document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement&&!nativeFull&&ready)stop();else invalidate();});
addEventListener('pagehide',()=>send({type:'stop'}));
for(const type of ['focus','blur'])addEventListener(type,()=>send({type:'focus',active:Number(document.hasFocus())}));
setInterval(()=>send({type:'heartbeat'}),1000);
if(debug){$('debug').hidden=false;document.body.classList.add('debug');}
connect();
