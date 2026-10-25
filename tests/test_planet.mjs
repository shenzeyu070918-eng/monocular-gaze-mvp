import assert from 'node:assert/strict';
import {planetField,ringField,galaxyField,textField,ringRadius,keplerSpeed,maskDistance,ringGap,ringLight,grain,particleBudget} from '../static/galaxy/particle-fields.mjs';
const o={seed:918,radius:1.5,ring_inner_ratio:1.4,ring_outer_ratio:8,ring_density_power:1.5,ring_thickness:.035,galaxy_radius:5.2,galaxy_arm_width:.4,text_width:7.2,text_depth:.35,text_flow:.06};
const sphere=planetField(16000,o);let inner=0,volumeMean=0;
for(let i=0;i<sphere.count;i++){
 const r=Math.hypot(...sphere.position.slice(i*3,i*3+3));assert.ok(r<=o.radius+1e-6);
 inner+=Number(r<o.radius*.5);volumeMean+=(r/o.radius)**3;
}
assert.ok(Math.abs(inner/sphere.count-.125)<.02);assert.ok(Math.abs(volumeMean/sphere.count-.5)<.02);
assert.deepEqual(planetField(50,o),planetField(50,o));assert.notDeepEqual(planetField(50,o).position,planetField(50,{...o,seed:919}).position);
const ring=ringField(20000,o),inside=o.radius*o.ring_inner_ratio,outside=o.radius*o.ring_outer_ratio;
let near=0,far=0;
for(let i=0;i<ring.count;i++){
 const [x,y,z]=ring.position.slice(i*3,i*3+3),r=Math.hypot(x,z);
 assert.ok(r>=inside-1e-5&&r<=outside+1e-5);assert.ok(Math.abs(y)<=o.radius*o.ring_thickness*1.5+1e-6);
 near+=Number(r<inside*2);far+=Number(r>outside/2);
}
assert.ok(near>0&&far>0);
// Counts per annular area, not raw counts: the outside really is less dense.
assert.ok(near/((inside*2)**2-inside**2)>2*far/(outside**2-(outside/2)**2));assert.equal(ringRadius(0,inside,outside,2),inside);
assert.ok(Math.abs(ringRadius(1,inside,outside,2)-outside)<1e-10);
assert.ok(Math.abs(keplerSpeed(inside*4,inside,.34)-.34/8)<1e-12);
const galaxy=galaxyField(1000,o);assert.ok(galaxy.position.every(Number.isFinite));assert.ok(galaxy.position.some((v,i)=>i%3===1&&Math.abs(v)>.05));
const width=20,height=12,mask=new Uint8Array(width*height);
for(let y=2;y<10;y++)for(let x=2;x<18;x++)mask[y*width+x]=1;
const distance=maskDistance(mask,width,height);assert.equal(distance[0],0);assert.ok(distance[6*width+10]>3);
const text=textField(2000,o,mask,width,height);let centers=0;
for(let i=0;i<text.count;i++){
 const [x,y,z]=text.position.slice(i*3,i*3+3);
 assert.ok(Math.abs(x)<=o.text_width/2);assert.ok(Math.abs(z)<=o.text_depth/2+1e-6);
 centers+=Number(Math.abs(x)<o.text_width*.25&&Math.abs(y)<1);
 assert.ok(text.flow[i]>=0&&text.flow[i]<=o.text_flow+1e-7);
}
assert.ok(centers>text.count*.1);assert.throws(()=>textField(10,o,new Uint8Array(9),3,3));
console.log('planet volume, ring density/bounds/Kepler motion, filled text clearance and deterministic fields passed');

const gradient={...o,ring_density_power:2.6,ring_gap_centers:[2.25,3.3],ring_gap_width:.07,ring_gap_depth:.85,ring_light_power:1.5,ring_outer_light:.15,bright_fraction:.015,bright_size:1.9,bright_intensity:2};
assert.ok(ringGap(2.25*o.radius,gradient)<.16);assert.ok(ringGap(2.6*o.radius,gradient)>.99);
const layered=ringField(40000,gradient);let gap=0,adjacent=0;
for(let i=0;i<layered.count;i++){const r=Math.hypot(layered.position[i*3],layered.position[i*3+2])/o.radius;gap+=Number(Math.abs(r-2.25)<.04);adjacent+=Number(Math.abs(r-2.5)<.04);}
assert.ok(gap<adjacent*.5);assert.equal(ringLight(o.radius*o.ring_inner_ratio,gradient),1);
assert.equal(ringLight(o.radius*o.ring_outer_ratio,gradient),.15);
let rare=0;for(let i=0;i<10000;i++){const g=grain((i+.5)/10000,gradient);rare+=Number(g.rare);assert.ok(g.size>0&&g.light>0);}
assert.equal(rare,150);assert.ok(grain(.99999,gradient).size>grain(.5,gradient).size*2);
console.log('ring density gaps, radial light falloff, and 1.5% long-tail grain population passed');

const full=particleBudget(230000,{planet_particles:80000,ring_particles:150000});assert.deepEqual(full,{planet:80000,ring:150000,intensity:1});
const low=particleBudget(100000,{planet_particles:80000,ring_particles:150000});assert.equal(low.planet+low.ring,100000);assert.ok(Math.abs(low.planet/low.ring-8/15)<.0001);assert.ok(low.intensity>1&&low.intensity<2);
