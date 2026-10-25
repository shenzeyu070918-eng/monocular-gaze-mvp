// Pure display logic: no camera, window geometry conversion, or network.
export class QualityController {
  constructor(o) { this.o=o; this.particles=o.particle_count; this.ratio=o.pixel_ratio; this.lowSince=null; this.last=null; this.samples=[]; this.downgrades=0; }
  frame(now,visible=true) {
    if(!visible){this.last=null;this.lowSince=null;this.samples=[];return false;}
    if(this.last===null){this.last=now;return false;}
    const dt=now-this.last;this.last=now;if(dt<=0)return false;
    this.samples.push([now,dt]);while(this.samples.length>1 && now-this.samples[0][0]>1000)this.samples.shift();
    const fps=1000*this.samples.length/this.samples.reduce((s,x)=>s+x[1],0);
    if(fps>=this.o.low_fps){this.lowSince=null;return false;}
    if(this.lowSince===null)this.lowSince=now-dt;
    if(now-this.lowSince<this.o.low_fps_s*1000)return false;
    this.lowSince=now;
    if(this.particles>50000)this.particles=50000;
    else if(this.ratio>this.o.min_pixel_ratio)this.ratio=Math.max(this.o.min_pixel_ratio,this.ratio*.75);
    else return false;
    this.downgrades++;return true;
  }
}

export class Intro {
  constructor(seconds){this.duration=seconds*1000;this.start=null;}
  begin(now){this.start=now;}
  progress(now){return this.start===null?0:Math.max(0,Math.min(1,(now-this.start)/this.duration));}
  interactive(now){return this.start!==null && this.progress(now)>=1;}
}

export function handPhysics(frame) {
  if(!frame || !frame.control || !frame.hands.length)return {collapse:0,explode:0,rx:0,ry:0,scale:1};
  const hands=frame.hands,opening=hands.reduce((s,h)=>s+h[2],0)/hands.length;
  return {collapse:Math.max(0,1-opening*2),explode:Math.max(0,(opening-.45)/.55),
    rx:hands.reduce((s,h)=>s+(h[1]-.5),0)/hands.length,
    ry:hands.reduce((s,h)=>s+(h[0]-.5),0)/hands.length,
    scale:hands.length===2?Math.max(.5,Math.min(2,frame.distance*3)):1};
}

export class Spring2D {
  constructor(){this.p=[0,0];this.v=[0,0];}
  step(target,dt,hz){const w=2*Math.PI*hz,decay=Math.exp(-w*dt);
    for(let i=0;i<2;i++){const off=this.p[i]-target[i],a=this.v[i]+w*off;
      this.p[i]=target[i]+(off+a*dt)*decay;this.v[i]=(this.v[i]-w*a*dt)*decay;}
    return this.p;
  }
}
export function nextPattern(id){return (id+1)%4;}
export function usableFrame(frame,revision,ageMs,limit){
  if(!frame || frame.viewport!==revision)return false;
  const processingMs=(frame.completed_us-frame.capture_us)/1000;
  return Number.isFinite(processingMs) && processingMs>=0 && processingMs+ageMs<=limit;
}
