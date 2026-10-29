import assert from 'node:assert/strict';
import {CaptureDwell,PlanetDirector} from '../static/galaxy/planet-controls.mjs';
const frame=(t,xy=[500,400])=>({gaze_valid:1,intro:0,capture_us:t*1000,xy});
const dwell=new CaptureDwell(1500,400);
for(let ms=0;ms<=1500;ms+=100)assert.equal(dwell.feed(frame(ms),true),ms===1500);
assert.equal(dwell.feed(frame(1600),false),false);
dwell.reset();dwell.feed(frame(0),true);
for(let i=0;i<200;i++)assert.equal(dwell.feed(frame(0),true),false); // rAF never accumulates capture dwell.
assert.equal(dwell.feed(frame(2000),true),false); // stale capture gap resets.
assert.equal(dwell.feed({...frame(2100),gaze_valid:0},true),false);
assert.equal(dwell.feed({...frame(2200),intro:1},true),false);
const options={stale_ms:400,interaction:{zoom_dwell_ms:1500,camera_spring_hz:.7,zoom_spring_hz:.65,yaw_range_deg:35,pitch_range_deg:35,gaze_fade_s:.5}};
const director=new PlanetDirector(options);let result;
for(let ms=0;ms<3000;ms+=50)result=director.update(frame(ms,[900,80]),.05,[1000,800],true,1);
assert.ok(result.yaw>25&&result.yaw<29);assert.ok(result.pitch>25&&result.pitch<29);assert.ok(result.zoom>.8);
for(let i=0;i<100;i++)result=director.update(null,.05,[1000,800],false,1);
assert.ok(result.zoom<.001&&result.eyeAlpha<.001);assert.ok(Math.abs(result.yaw)<.001);
director.reset();result=director.update(frame(5000),.05,[1000,800],true,.5);
assert.equal(result.eye,false);assert.equal(result.zoom,0);
console.log('capture-clock dwell, duplicate/gap/intro rejection, orbit direction, smooth zoom and gaze fade passed');

const {planetHandTargets,HandDynamics,handDiagnostic}=await import('../static/galaxy/planet-controls.mjs');
const handOptions={interaction:{hand_speed_scale:.6,spin_boost:3,min_hand_scale:.65,max_hand_scale:1.5,hand_response_s:.55}};
const open={control:1,hand_available:1,hand_error:0,intro:0,hands:[[.5,.5,.9,.1,1.,0,1]],distance:0,hand_detected:1,hand_openings:[.9]};
assert.equal(planetHandTargets(open,handOptions).scatter,.9);
assert.equal(planetHandTargets(open,handOptions).spin,3);
assert.equal(planetHandTargets({...open,hand_error:2},handOptions).scatter,0);
assert.equal(planetHandTargets({...open,hands:[[.5,.5,.5,.1,0,0,0]]},handOptions).scatter,0); // Half-open cannot flap modes.
const fist={...open,hands:[[.5,.5,.1,.1,0,0,-1]]};assert.equal(planetHandTargets(fist,handOptions).collapse,1);
assert.equal(planetHandTargets({...open,hands:[...open.hands,...open.hands],distance:1},handOptions).scale,1.5);
const dynamics=new HandDynamics(handOptions);let h;
for(let i=0;i<60;i++)h=dynamics.update(open,.05);assert.ok(h.scatter>.89);
for(let i=0;i<60;i++)h=dynamics.update(fist,.05);assert.ok(h.collapse>.99&&h.scatter<.01);
for(let i=0;i<100;i++)h=dynamics.update(null,.05);assert.ok(h.collapse<.001&&h.scatter<.001&&h.spin<.001&&Math.abs(h.scale-1)<.001);
dynamics.reset();assert.equal(dynamics.update(open,.05,.3).scatter,0);
assert.match(handDiagnostic(open),/检测 1 手.*0.90/);assert.match(handDiagnostic({...open,hand_error:1}),/模型加载失败/);
assert.match(handDiagnostic(open,false),/过期/);
console.log('open scatter, fist restore, bounded velocity boost, two-hand scale, intro gating and diagnostic states passed');
