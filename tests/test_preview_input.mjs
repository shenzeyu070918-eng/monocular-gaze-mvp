import assert from 'node:assert/strict';
import {previewFrame,PreviewClock} from '../static/galaxy/preview-input.mjs';
const f=previewFrame(200000,[1512,950],2,[756,475],'open');
assert.equal(f.capture_us,200000);assert.equal(f.pattern,2);assert.equal(f.gaze_valid,true);assert.equal(f.hands[0][6],1);assert.equal(f.hands[0][2],1);
assert.equal(previewFrame(0,[1512,950],0,null,'fist').hands[0][6],-1);
assert.equal(previewFrame(0,[1512,950],0,null).hands.length,0);
const clock=new PreviewClock(),a=clock.sample(0,[1512,950],0,[756,475],'open');
assert.equal(clock.sample(50,[1512,950],0,null,'fist'),a); // rAF duplicates do not invent capture time.
const b=clock.sample(70,[1512,950],1,null,'fist');assert.notEqual(b,a);assert.equal(b.capture_us,70000);assert.equal(b.pattern,1);
clock.reset();assert.equal(clock.sample(71,[1512,950],2,null,'none').capture_us,71000);
console.log('camera-free synthetic hand/gaze values, independent 15Hz capture clock and reset passed');
