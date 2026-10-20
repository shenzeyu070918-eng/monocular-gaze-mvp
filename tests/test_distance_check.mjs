import assert from 'node:assert/strict';
import {DistanceCheck,cameraColumns,cameraStatus} from '../static/distance-check.js';

// All distances/times below are constructed test inputs, not hardware results.
const config={distances_cm:[40,50,60],settle_ms:10,duration_ms:30,min_samples:3,warning_cm:5};
const check=new DistanceCheck(config);
check.start(40,100);
check.add(999,109);check.add(999,141);check.add(NaN,120);check.add(true,120);
check.add(42,110);check.add(44,120);check.add(43,130);
assert.equal(check.ready(139),false);assert.throws(()=>check.finish(139));
const row=check.finish(140);
assert.deepEqual(row,{measured_cm:40,estimated_cm:43,bias_cm:3,bias_percent:7.5,sample_count:3,
 sample_start_ms:110,sample_end_ms:130,browser_time_ms:140,warning:false});
check.start(50,200);check.add(40,210);check.add(39,220);check.add(41,230);
assert.equal(check.finish(240).bias_cm,-10);
check.start(60,300);check.add(61,310);
const insufficient=check.finish(340);assert.equal(insufficient.estimated_cm,null);assert.equal(insufficient.warning,true);
assert.throws(()=>new DistanceCheck({...config,duration_ms:NaN}));
const fallback={intrinsics_source:'estimated',device_file:'test.yaml',intrinsics_reason:'aspect_ratio_mismatch',
 undistort_enabled:true,undistort_applied:false,scaled:false,width_px:640,height_px:360};
assert.match(cameraStatus(fallback),/长宽比不同/);
assert.match(cameraStatus(fallback),/当前未应用/);
assert.deepEqual(cameraColumns(fallback),{intrinsics_source:'estimated',device_file:'test.yaml',
 intrinsics_reason:'aspect_ratio_mismatch',undistort_enabled:true,undistort_applied:false,
 intrinsics_scaled:false,camera_width_px:640,camera_height_px:360});
console.log('synthetic distance windows, signed bias, insufficient samples and camera provenance passed');
