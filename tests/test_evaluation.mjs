import assert from 'node:assert/strict';
import {evaluationTargets,evaluationFrames} from '../static/evaluation.js';
import {stagePointToScreen,screenPointToStage} from '../static/recording.js';
const config={center_points:[[.25,.25],[.75,.75]],calibration_points:[[.5,.5],[.12,.12],[.88,.88]]};
const targets=evaluationTargets(config,.05,()=>.25);
assert.equal(targets.length,10);assert.equal(new Set(targets.map(p=>p.target_id)).size,10);
assert.equal(targets.filter(p=>p.region==='edge').length,8);
assert.ok(targets.some(p=>p.target[0]===.05&&p.target[1]===.05));assert.ok(targets.some(p=>p.target[0]===.95&&p.target[1]===.95));
assert.notDeepEqual(targets.map(p=>p.target_id),evaluationTargets(config,.05,()=>.75).map(p=>p.target_id));
assert.throws(()=>evaluationTargets({...config,center_points:[[.5,.5]]},.05),/重合/);
for(const device_pixel_ratio of [1,1.25,2])for(const fullscreen of [true,false]){
 const screen={width_px:1512,height_px:982,device_pixel_ratio,fullscreen,stage_left_px:100,stage_top_px:80,stage_width_px:700,stage_height_px:450,screen_x:10,screen_y:30,outer_width_px:1000,window_width_px:980,outer_height_px:800,window_height_px:730};
 for(const point of [[.05,.95],[.5,.5],[-.3,1.4]]){
  const result=stagePointToScreen(screenPointToStage(point,screen),screen);
  result.forEach((v,i)=>assert.ok(Math.abs(v-point[i])<1e-12));
 }
}
assert.deepEqual(evaluationFrames([{target_id:'e',valid:true,raw_screen_x:-.2,raw_screen_y:1.2,origin_x_m:0,origin_y_m:0,origin_z_m:.5}]),[{target_id:'e',valid:true,raw:[-.2,1.2],origin_m:[0,0,.5]}]);
assert.equal(evaluationFrames([{target_id:'e',valid:false}])[0].origin_m,null);
console.log('full-screen targets, coordinate roundtrips, DPI and unclipped summary inputs passed');
