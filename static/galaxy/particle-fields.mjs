// Seeded volumes, not meshes/outline samples. No browser or input dependencies.
export function randomSource(seed){let s=seed>>>0;return()=>{s=(Math.imul(s,1664525)+1013904223)>>>0;return s/4294967296;};}
function field(count){return {count,position:new Float32Array(count*3),seed:new Float32Array(count*4),flow:new Float32Array(count)};}
function direction(rand){const y=rand()*2-1,a=rand()*Math.PI*2,r=Math.sqrt(1-y*y);return [r*Math.cos(a),y,r*Math.sin(a)];}
function noiseSeed(f,i,rand){for(let j=0;j<4;j++)f.seed[i*4+j]=rand();}
export function ringRadius(u,inner,outer,power){const p=2-power;return Math.abs(p)<1e-8?inner*(outer/inner)**u:(inner**p+u*(outer**p-inner**p))**(1/p);}
export function keplerSpeed(radius,inner,speed){return speed*(inner/radius)**1.5;}
export function planetField(count,o){
 const f=field(count),rand=randomSource(o.seed);
 for(let i=0;i<count;i++){const d=direction(rand),r=o.radius*Math.cbrt(rand());f.position.set(d.map(x=>x*r),i*3);noiseSeed(f,i,rand);}
 return f;
}
export function ringGap(radius,o){
 let density=1;for(const center of o.ring_gap_centers??[])density*=1-o.ring_gap_depth*Math.exp(-.5*((radius/o.radius-center)/o.ring_gap_width)**2);return density;
}
export function ringLight(radius,o){const u=Math.max(0,Math.min(1,(radius/o.radius-o.ring_inner_ratio)/(o.ring_outer_ratio-o.ring_inner_ratio)));return o.ring_outer_light+(1-o.ring_outer_light)*(1-u)**o.ring_light_power;}
export function grain(seed,o){const tail=seed>1-o.bright_fraction?(seed-(1-o.bright_fraction))/o.bright_fraction:0;return {rare:tail>0,size:.45+.5*Math.sqrt(seed)+(o.bright_size-1)*tail,light:.55+.8*seed**2.2+(o.bright_intensity-1)*tail};}
export function ringField(count,o){
 const f=field(count),rand=randomSource(o.seed+1),inner=o.radius*o.ring_inner_ratio,outer=o.radius*o.ring_outer_ratio;
 for(let i=0;i<count;i++){
  let r;do{r=ringRadius(rand(),inner,outer,o.ring_density_power);}while(rand()>ringGap(r,o));
  const a=rand()*Math.PI*2;
  const y=(rand()+rand()+rand()-1.5)*o.radius*o.ring_thickness;
  f.position.set([r*Math.cos(a),y,r*Math.sin(a)],i*3);noiseSeed(f,i,rand);
 }
 return f;
}
export function galaxyField(count,o){
 const f=field(count),rand=randomSource(o.seed+2);
 for(let i=0;i<count;i++){
  const bulge=rand()<.12;
  if(bulge){const d=direction(rand),r=Math.cbrt(rand())*o.radius*.85;f.position.set([d[0]*r,d[1]*r*.6,d[2]*r],i*3);}
  else{
   const r=o.galaxy_radius*Math.sqrt(rand()),a=r*.95+(i%4)*Math.PI/2+(rand()+rand()-1)*o.galaxy_arm_width;
   f.position.set([Math.cos(a)*r,(rand()+rand()-1)*o.radius*.1,Math.sin(a)*r],i*3);
  }
  noiseSeed(f,i,rand);
 }
 return f;
}
// Distance to the outside of filled glyphs; limits flow to each stroke's interior.
export function maskDistance(mask,width,height){
 const d=new Float32Array(mask.length),big=width+height;
 for(let y=0;y<height;y++)for(let x=0;x<width;x++){const i=y*width+x;d[i]=mask[i]&&x&&y&&x<width-1&&y<height-1?big:0;}
 for(let y=1;y<height;y++)for(let x=1;x<width;x++){const i=y*width+x;if(d[i])d[i]=Math.min(d[i],d[i-1]+1,d[i-width]+1,d[i-width-1]+Math.SQRT2);}
 for(let y=height-2;y>=0;y--)for(let x=width-2;x>=0;x--){const i=y*width+x;if(d[i])d[i]=Math.min(d[i],d[i+1]+1,d[i+width]+1,d[i+width+1]+Math.SQRT2);}
 return d;
}
export function textField(count,o,mask,width,height){
 const f=field(count),rand=randomSource(o.seed+3),inside=[],d=maskDistance(mask,width,height);
 let left=width,right=0,top=height,bottom=0;
 for(let y=0;y<height;y++)for(let x=0;x<width;x++)if(mask[y*width+x]){inside.push(y*width+x);left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);}
 if(!inside.length)throw new Error('配置文字没有可绘制笔画');
 const scale=o.text_width/(right-left+1);
 f.bounds=[-o.text_width/2,o.text_width/2,-(bottom-top+1)*scale/2,(bottom-top+1)*scale/2];
 for(let i=0;i<count;i++){
  const p=inside[Math.floor(rand()*inside.length)],x=p%width,y=Math.floor(p/width);
  f.position.set([(x-(left+right)/2)*scale,((top+bottom)/2-y)*scale,(rand()-.5)*o.text_depth],i*3);
  f.flow[i]=Math.min(o.text_flow,Math.max(0,d[p]-.75)*scale*.45);noiseSeed(f,i,rand);
 }
 return f;
}

export function particleBudget(count,o){
 const total=o.planet_particles+o.ring_particles;
 const planet=Math.max(1,Math.min(count-1,Math.round(count*o.planet_particles/total)));
 // Partial radiance compensation keeps the low tier readable without a white wash.
 return {planet,ring:count-planet,intensity:Math.sqrt(total/count)};
}
