import assert from 'node:assert/strict';
import {JUMP_ANCHORS,generateJumpPoints,pointDistance} from '../static/jump.js';
function randomGenerator(seed){let current=seed;return ()=>{current=(current*1664525+1013904223)>>>0;return current/2**32;};}
for(let seed=1;seed<=30;seed++){
 const points=generateJumpPoints({point_count:25,dwell_ms:1200,min_distance:.35},randomGenerator(seed));
 assert.equal(points.length,25);
 for(const anchor of JUMP_ANCHORS)assert.ok(points.some(p=>p[0]===anchor[0]&&p[1]===anchor[1]));
 points.forEach(p=>p.forEach(v=>assert.ok(v>=0&&v<=1)));
 for(let i=1;i<points.length;i++)assert.ok(pointDistance(points[i-1],points[i])>=.35);
}
assert.throws(()=>generateJumpPoints({point_count:7,dwell_ms:1,min_distance:.1}),/至少为 8/);
assert.throws(()=>generateJumpPoints({point_count:8,dwell_ms:0,min_distance:.1}),/停留时间/);
assert.throws(()=>generateJumpPoints({point_count:8,dwell_ms:1,min_distance:0}),/最小间距/);
assert.throws(()=>generateJumpPoints({point_count:8,dwell_ms:1,min_distance:2}),/最小间距/);
assert.throws(()=>generateJumpPoints({point_count:8,dwell_ms:1,min_distance:1.3}),/无法同时覆盖/);
assert.equal(pointDistance([0,0],[1,1]),Math.SQRT2);
console.log('jump boundary coverage, randomized ordering and minimum-distance tests passed');
