import assert from 'node:assert/strict';
import {affine,summary,errorDistance} from '../static/math.js';
assert.deepEqual(affine([2,3],[[2,0],[0,3],[1,-1]]),[5,8]);
assert.equal(errorDistance([1.2,0],[.2,0],100,50),100);
assert.equal(summary([null,NaN,1,3]).mean,2);
assert.equal(summary([1,2,3]).median,2);
assert.equal(summary([]),null);
console.log('5 math assertions passed');
