import assert from 'node:assert/strict';
import {CaptureDwell} from '../static/galaxy/planet-controls.mjs';
import {coreTarget,projectedRadius} from '../static/galaxy/zoom-hit.mjs';
const i={zoom_core_scale:1,zoom_error_scale:2.5,zoom_viewport_ratio:.12,zoom_exit_ratio:1.6};
const f=t=>({gaze_valid:1,intro:0,capture_us:t*1000});
for(const scene of ['galaxy','text']){
 const d=new CaptureDwell(1500,400,300,400);
 for(let t=0;t<=1500;t+=100){const hit=coreTarget(scene,[500+Math.sin(t)*60,400+Math.cos(t)*60],[500,400],30,[40,40],[1000,800],i);assert.ok(hit.inside);d.feed(f(t),hit.inside,hit.retain);}
 assert.ok(d.active); // jitter is 1.5 error radii, still inside the 2.5x target.
}
const d=new CaptureDwell(1500,400,300,400);
for(let t=0;t<=700;t+=100)d.feed(f(t),true);
d.feed(f(800),false);d.feed(f(1000),false);d.feed(f(1100),true);assert.equal(d.elapsed,700000);
for(let t=1200;t<=1900;t+=100)d.feed(f(t),true);assert.ok(d.active);
d.feed(f(2000),false,true);assert.ok(d.active); // between enter and exit radius stays zoomed.
d.feed(f(2100),false,false);d.feed(f(2500),false,false);assert.ok(d.active);
d.feed(f(2501),false,false);assert.equal(d.active,false);
const target=coreTarget('galaxy',[655,400],[500,400],30,[40,40],[1000,800],i);assert.equal(target.inside,false);assert.equal(target.retain,true);
const planet=coreTarget('planet',[500,400],[500,400],30,[100,100],[1000,800],i,false,false);assert.equal(planet.inside,false);assert.equal(planet.radius,30);
assert.ok(Math.abs(projectedRadius(1,5,90,1000)-100)<1e-10);
const g=new CaptureDwell(1500,400,300,400);g.feed(f(0),true);g.feed(f(100),true);for(let n=0;n<99;n++)g.feed(f(100),true);assert.equal(g.elapsed,100000);
g.feed(f(501),true);assert.equal(g.elapsed,0);
console.log('screen core tolerance, 1.5-radius jitter, frozen grace, expanded exit hysteresis, duplicates and capture gap passed');
