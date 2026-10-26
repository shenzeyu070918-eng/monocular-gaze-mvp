// Display-only projection controls. Input xy is already viewport-local from Python.
import {Spring2D} from './logic.mjs';
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
export class CaptureDwell{
 constructor(durationMs,staleMs){this.duration=durationMs*1000;this.gap=staleMs*1000;this.reset();}
 reset(){this.last=null;this.since=null;this.active=false;}
 feed(frame,inside){
  if(!frame?.gaze_valid||frame.intro){this.reset();return false;}
  const t=frame.capture_us;
  if(this.last!==null&&t<=this.last)return this.active;
  if(this.last!==null&&t-this.last>this.gap){this.since=null;this.active=false;}
  this.last=t;
  if(!inside){this.since=null;this.active=false;return false;}
  if(this.since===null)this.since=t;
  return this.active=t-this.since>=this.duration;
 }
}
export class PlanetDirector{
 constructor(options){this.o=options;this.camera=new Spring2D();this.zoom=new Spring2D();this.eyeAlpha=0;this.scene=null;this.dwell=new CaptureDwell(options.interaction.zoom_dwell_ms,options.stale_ms);}
 reset(){this.camera=new Spring2D();this.zoom=new Spring2D();this.eyeAlpha=0;this.scene=null;this.dwell.reset();}
 update(frame,dt,size,inside,intro=1,scene='planet'){
  if(scene!==this.scene){this.scene=scene;this.dwell.reset();this.zoom=new Spring2D();}
  const o=this.o,i=o.interaction,usable=intro>=1&&frame?.intro===0,eye=usable&&!!frame.gaze_valid;
  const yaw=scene==='text'?i.text_yaw_range_deg:scene==='galaxy'?i.galaxy_rotation_range_deg:i.yaw_range_deg,pitch=scene==='text'?i.text_pitch_range_deg:scene==='galaxy'?i.galaxy_pitch_range_deg:i.pitch_range_deg;
  const target=eye?[clamp(frame.xy[0]/size[0]*2-1,-1,1)*yaw,clamp(1-frame.xy[1]/size[1]*2,-1,1)*pitch]:[0,0];
  const angles=this.camera.step(target,dt,i.camera_spring_hz);
  const zoom=this.dwell.feed(eye?frame:null,inside);
  const z=this.zoom.step([Number(zoom),0],dt,i.zoom_spring_hz)[0];
  this.eyeAlpha+=(Number(eye)-this.eyeAlpha)*(1-Math.exp(-dt/i.gaze_fade_s));
  return {yaw:clamp(angles[0],-yaw,yaw),pitch:clamp(angles[1],-pitch,pitch),zoom:clamp(z,0,1),eyeAlpha:this.eyeAlpha,eye};
 }
}

export function planetHandTargets(frame,options){
 const i=options.interaction;
 if(!frame?.control||!frame.hand_available||frame.hand_error||!frame.hands?.length)return {scatter:0,collapse:0,spin:0,scale:1};
 const hands=frame.hands;
 const opening=hands.reduce((s,h)=>s+(h[6]===1?h[2]:0),0)/hands.length;
 const collapse=hands.reduce((s,h)=>s+Number(h[6]===-1),0)/hands.length;
 const speed=hands.reduce((s,h)=>s+Math.hypot(h[4],h[5]),0)/hands.length;
 return {scatter:opening,collapse,spin:Math.min(1,speed/i.hand_speed_scale)*i.spin_boost,
  scale:hands.length===2?clamp(frame.distance*3,i.min_hand_scale,i.max_hand_scale):1};
}
export class HandDynamics{
 constructor(options){this.o=options;this.reset();}
 reset(){this.state={scatter:0,collapse:0,spin:0,scale:1};}
 update(frame,dt,intro=1){const target=planetHandTargets(intro>=1&&frame?.intro===0?frame:null,this.o),a=1-Math.exp(-dt/this.o.interaction.hand_response_s);
  for(const key of Object.keys(this.state))this.state[key]+=(target[key]-this.state[key])*a;
  return this.state;
 }
}
export function handDiagnostic(frame,usable=true){
 if(!frame)return '尚未收到数值帧';
 const status=frame.hand_error===1?'模型加载失败':frame.hand_error===2?'模型检测失败':frame.hand_available?'模型就绪':'模型未就绪';
 return status+' · 检测 '+frame.hand_detected+' 手 · 张开度 '+(frame.hand_openings?.map(v=>v.toFixed(2)).join(' / ')||'—')+
  ' · 接管 '+(frame.control?'是':'否')+(usable?'':' · 帧已过期/视口无效');
}
