// Two logarithmic density-wave arms in an exponential stellar disk. Built once.
import {randomSource} from './particle-random.mjs';
const TAU=Math.PI*2,mix=(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t);
const normal=rand=>Math.sqrt(-2*Math.log(Math.max(1e-12,rand())))*Math.cos(TAU*rand());
const wrap=a=>Math.atan2(Math.sin(a),Math.cos(a));
function linear(hex){return [1,3,5].map(i=>{const c=parseInt(hex.slice(i,i+2),16)/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;});}
function palette(colors,t){return t<.5?mix(colors[0],colors[1],t*2):mix(colors[1],colors[2],(t-.5)*2);}
export function spiralAngle(r,o){return Math.log(r/(o.galaxy_radius*.15))/Math.tan(o.galaxy_pitch_deg*Math.PI/180);}
export function exponentialRadius(rand,scale,outer,inner=0){let r;do{r=-scale*Math.log(Math.max(1e-12,rand()*rand()));}while(r>outer||r<inner);return r;}
export function dustLane(offset,o){return 1-o.galaxy_dust_depth*Math.exp(-.5*((offset-.65)/.22)**2);}
function noise(x){const i=Math.floor(x),t=x-i,s=t*t*(3-2*t),h=n=>{const v=Math.sin(n*127.1+311.7)*43758.5453;return v-Math.floor(v);};return h(i)+(h(i+1)-h(i))*s;}
export function galaxyField(count,o){
 const f={count,position:new Float32Array(count*3),seed:new Float32Array(count*4),flow:new Float32Array(count),color:new Float32Array(count*4),arm:new Uint8Array(count)};
 const rand=randomSource(o.seed+2),R=o.galaxy_radius,B=R*o.galaxy_bulge_radius,h=R*o.galaxy_disk_scale;
 const warm=o.planet_colors.map(linear),blue=o.galaxy_arm_colors.map(linear),pink=o.galaxy_knot_colors.map(linear);
 const bulge=Math.round(count*o.galaxy_bulge_fraction),knots=Math.round(count*o.galaxy_knot_fraction),disk=count-bulge-knots;
 const diffuse=Math.round(disk*o.galaxy_diffuse_fraction),branch=Math.round((disk-diffuse)*o.galaxy_branch_fraction),main=disk-diffuse-branch;
 f.counts={main,branch,diffuse,bulge,knots};
 const clusterRand=randomSource(o.seed+42),clusters=Array.from({length:o.galaxy_knot_clusters},(_,i)=>{const r=R*(.32+clusterRand()*.6),a=spiralAngle(r,o)+(i%2)*Math.PI;return [r*Math.cos(a),r*Math.sin(a)];});
 for(let i=0;i<count;i++){
  // flow is a population tag here (1 main arm, 2 branch, 0 diffuse, 3 bulge, 4 HII).
  const type=i<main?1:i<main+branch?2:i<main+branch+diffuse?0:i<count-knots?3:4;f.flow[i]=type;
  let r,a,y,branchStart=0,dust=1,fragment=1,brightness=1,arm=Math.floor(rand()*2);
  if(type===3){
   const u=rand()*2-1,phi=TAU*rand(),q=B*rand()**.62,s=Math.sqrt(1-u*u);
   f.position.set([q*s*Math.cos(phi),q*u*o.galaxy_bulge_flattening,q*s*Math.sin(phi)],i*3);
   r=Math.hypot(f.position[i*3],f.position[i*3+2]);brightness=o.galaxy_core_intensity*(1-(q/B)**2)**1.3;
  }else if(type===4){
   const c=clusters[i%clusters.length],width=R*o.galaxy_knot_size,x=c[0]+normal(rand)*width,z=c[1]+normal(rand)*width;
   f.position.set([x,normal(rand)*width*.25,z],i*3);r=Math.hypot(x,z);brightness=.9+rand()*.6;
  }else{
   if(type===2){const segment=Math.floor(rand()*3);branchStart=.3+segment*.2;r=R*Math.max(.2,Math.min(.98,.4+segment*.2+normal(rand)*.055));}
   else r=exponentialRadius(rand,h,R,type===1?R*.15:0);
   const width=o.galaxy_arm_width*(.6+.9*r/R)*(.65+.7*noise(r/R*11+arm*9)),curve=spiralAngle(Math.max(r,R*.02),o)+arm*Math.PI;
   let offset;
   do{offset=type===0?wrap(2*(TAU*rand()-curve))*.5*r/width:normal(rand);dust=dustLane(offset,o);}while(rand()>dust);
   a=curve+offset*width/r;
   if(type===2)a+=.55*Math.max(0,Math.min(1,(r/R-branchStart)/.2))**2;
   fragment=1-o.galaxy_fragmentation*Math.min(1,(r/R)**.7)*(1-noise(r/R*24+arm*19));
   y=normal(rand)*R*.015;f.position.set([r*Math.cos(a),y,r*Math.sin(a)],i*3);
   brightness=(o.galaxy_outer_brightness+(1-o.galaxy_outer_brightness)*(1-r/R)**.7)*dust*fragment*(type===0?o.galaxy_diffuse_brightness:1);
  }
  f.arm[i]=arm;
  let color;
  if(type===3)color=palette(warm,.25+rand()*.5);
  else if(type===4)color=palette(pink,rand());
  else{const gold=Math.exp(-((r/(B*1.15))**2));color=mix(palette(blue,rand()),palette(warm,.2+rand()*.45),gold);if(type===0)color=mix(color,palette(warm,.6),.15);}
  f.color.set([...color,brightness],i*4);
  for(let j=0;j<4;j++)f.seed[i*4+j]=rand();
 }
 return f;
}
