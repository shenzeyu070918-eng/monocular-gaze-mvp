import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ChargeFeedback} from '../static/galaxy/charge-feedback.mjs';
import {CaptureDwell} from '../static/galaxy/planet-controls.mjs';
const o={charge_response_s:.12,charge_release_s:.45};
const q=new ChargeFeedback(o);let previous=0;
for(let n=0;n<100;n++){const v=q.step(.5,.016);assert.ok(v>=previous&&v<=.5);previous=v;}
assert.ok(q.level>.4999);const phase=q.phase;
const first=q.step(0,.016);assert.ok(first>0&&first<previous);assert.ok(q.phase>phase);
previous=first;for(let n=0;n<250;n++){const v=q.step(0,.016);assert.ok(v<=previous&&v>=0);previous=v;}
assert.ok(q.level<.0001);
q.reset();assert.equal(q.level,0);assert.equal(q.phase,0);assert.equal(q.step(1,0),0);
const d=new CaptureDwell(1500,400,300,400),f=t=>({gaze_valid:1,intro:0,capture_us:t*1000});
for(let t=0;t<=750;t+=50){d.feed(f(t),true);q.step(d.progress,.05);}
const held=d.progress;
for(let t=800;t<=1000;t+=50){d.feed(f(t),false);q.step(d.progress,.05);assert.equal(d.progress,held);}
d.feed(f(1100),true);assert.equal(d.progress,held); // grace endpoint: no new evidence
for(let t=1150;t<=1900;t+=50){d.feed(f(t),true);q.step(d.progress,.05);}
assert.ok(d.active);d.feed(f(1950),false,false);
for(let t=2000;t<=2350;t+=50){d.feed(f(t),false,false);q.step(d.progress,.05);assert.ok(d.active);}
const before=q.level;d.feed(f(2400),false,false);assert.equal(d.active,false);q.step(d.progress,.05);assert.ok(q.level>0&&q.level<before);
// Purely visual: no DOM ring or extra WebGL pass; edge indicator stays separate.
for(const name of ['app.mjs','preview.mjs','style.css'])assert.ok(!readFileSync(new URL('../static/galaxy/'+name,import.meta.url),'utf8').includes('zoom-progress'));
assert.ok(readFileSync(new URL('../static/galaxy/style.css',import.meta.url),'utf8').includes('.edge-meter'));
console.log('scene charge rises/decays continuously, integrates phase without jumps, preserves dwell grace/hysteresis and removes UI ring');
