import assert from 'node:assert/strict';
import {planeHit,sceneHit,sceneCamera} from '../static/galaxy/scene-geometry.mjs';
import {PlanetDirector} from '../static/galaxy/planet-controls.mjs';
const visual={radius:1.5,galaxy_radius:5.2,camera_distance:7.8,close_distance:3.6,galaxy_distance:10.4,galaxy_close_distance:6.8,text_distance:7.8,text_close_distance:5.8,camera_pitch_deg:25,camera_roll_deg:-12};
assert.equal(sceneHit('planet',[0,0,8],[0,0,-1],visual).hit,true);
assert.equal(sceneHit('planet',[3,0,8],[0,0,-1],visual).hit,false);
assert.equal(sceneHit('planet',[2,0,8],[0,0,-1],visual,2).hit,true);
assert.equal(sceneHit('planet',[0,0,8],[0,0,1],visual).hit,false);
assert.deepEqual(planeHit([2,4,3],[0,-1,0],1),[2,0,3]);
assert.equal(planeHit([0,2,0],[1,0,0],1),null);
assert.equal(planeHit([0,2,0],[0,1,0],1),null);
assert.equal(sceneHit('galaxy',[3,5,1],[0,-1,0],visual).hit,true);
assert.equal(sceneHit('galaxy',[6,5,1],[0,-1,0],visual).hit,false);
assert.equal(sceneHit('galaxy',[6,5,1],[0,-1,0],visual,2).hit,true);
const bounds=[-3.6,3.6,-1.2,1.2];
assert.equal(sceneHit('text',[0,0,8],[0,0,-1],visual,1,bounds).hit,true);
assert.equal(sceneHit('text',[0,2,8],[0,0,-1],visual,1,bounds).hit,false);
assert.equal(sceneHit('text',[4,0,8],[0,0,-1],visual,1,bounds).hit,false);
assert.equal(sceneHit('text',[4,0,8],[0,0,-1],visual,2,bounds).hit,true);
assert.equal(sceneHit('text',[0,0,8],[0,0,1],visual,1,bounds).hit,false);
for(const scene of ['planet','galaxy','text'])assert.ok(sceneCamera(scene,visual).near<sceneCamera(scene,visual).far);
const options={stale_ms:400,interaction:{zoom_dwell_ms:1500,camera_spring_hz:.7,zoom_spring_hz:.65,yaw_range_deg:35,pitch_range_deg:35,text_yaw_range_deg:10,text_pitch_range_deg:8,gaze_fade_s:.5}};
const director=new PlanetDirector(options);let result;
for(const scene of ['planet','galaxy','text']){
 result=director.update({gaze_valid:1,intro:0,capture_us:0,xy:[900,100]},.05,[1000,800],true,1,scene);assert.equal(result.zoom,0);
 for(let ms=50;ms<=3000;ms+=50)result=director.update({gaze_valid:1,intro:0,capture_us:ms*1000,xy:[900,100]},.05,[1000,800],true,1,scene);
 assert.ok(result.zoom>.8);
 if(scene==='text'){assert.ok(result.yaw<=10&&result.pitch<=8);}
 for(let ms=3050;ms<=6000;ms+=50)result=director.update({gaze_valid:1,intro:0,capture_us:ms*1000,xy:[900,100]},.05,[1000,800],false,1,scene);
 assert.ok(result.zoom<.01);
}
console.log('sphere/disk/text-plane bounds, forward rays, scaled geometry, per-scene zoom and text angle limits passed');
