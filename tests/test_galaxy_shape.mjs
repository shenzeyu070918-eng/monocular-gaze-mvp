import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {galaxyField,dustLane,spiralAngle} from '../static/galaxy/galaxy-field.mjs';
import {galaxyPosition} from '../static/galaxy/galaxy-motion.mjs';
import {compareArms} from '../static/galaxy/galaxy-stability.mjs';
const o=JSON.parse(readFileSync(new URL('./fixtures/galaxy-visual.json',import.meta.url)));
const f=galaxyField(60000,o);assert.deepEqual(galaxyField(20,o),galaxyField(20,o));
assert.equal(Object.values(f.counts).reduce((a,b)=>a+b,0),f.count);
assert.ok(Math.abs(f.counts.diffuse/(f.counts.main+f.counts.branch+f.counts.diffuse)-.30)<.0001);
assert.ok(f.position.every(Number.isFinite)&&f.color.every(Number.isFinite));
assert.ok(f.color.some((v,n)=>n%4===0&&v>.5));assert.ok(dustLane(.65,o)<dustLane(-.65,o)*.5);
const radii=[.2,.3,.5,.8].map(v=>v*o.galaxy_radius);
for(let i=1;i<radii.length;i++)assert.ok(Math.abs((spiralAngle(radii[i],o)-spiralAngle(radii[i-1],o))/Math.log(radii[i]/radii[i-1])-1/Math.tan(15*Math.PI/180))<1e-10);
let inner=0,outer=0,bulgeCount=0,bulgeY=0,bulgeX=0;
for(let n=0;n<f.count;n++){
 const [x,y,z]=f.position.slice(n*3,n*3+3),r=Math.hypot(x,z);
 if(f.flow[n]===0){inner+=Number(r<o.galaxy_radius*.5);outer+=Number(r>=o.galaxy_radius*.5);}
 if(f.flow[n]===3){bulgeCount++;bulgeY+=y*y;bulgeX+=x*x;assert.ok(x*x+z*z+(y/o.galaxy_bulge_flattening)**2<=(o.galaxy_radius*o.galaxy_bulge_radius)**2+1e-6);}
}
assert.ok(inner/ (.5**2)>outer/(1-.5**2)*2);assert.ok(Math.abs(Math.sqrt(bulgeY/bulgeX)-o.galaxy_bulge_flattening)<.025);
const take=Array.from({length:2000},(_,n)=>n*3),sample=t=>Float64Array.from(take.flatMap(j=>galaxyPosition(f.position.slice(j,j+3),f.seed.slice(j/3*4,j/3*4+4),t,o)));
const base=sample(8);
for(const t of [8,60,300,600,2400,60000]){
 const points=sample(t),result=compareArms(base,points,8,t,o);assert.ok(result.passed,JSON.stringify(result));
 for(let j=0;j<points.length;j+=3){const r=Math.hypot(points[j],points[j+2]),r0=Math.hypot(f.position[take[j/3]],f.position[take[j/3]+2]);assert.ok(Math.abs(r/r0-1)<=o.galaxy_drift_fraction+1e-12);}
}
// Regression guard must reject the previous differential rotation, not just agree with itself.
const wrong=t=>Float64Array.from(take.flatMap(j=>{const p=f.position.slice(j,j+3),r=Math.hypot(p[0],p[2]),a=t*o.galaxy_spin/Math.max(r,.6)**.6;return [Math.cos(a)*p[0]+Math.sin(a)*p[2],p[1],-Math.sin(a)*p[0]+Math.cos(a)*p[2]];}));
assert.equal(compareArms(wrong(8),wrong(600),8,600,o).passed,false);
assert.throws(()=>compareArms([],[],8,60,o));
console.log('two log arms, exponential diffuse disk, dust lane, oblate bulge, bounded drift, rigid accelerated rotation and old winding regression rejection passed');
