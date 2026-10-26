import assert from 'node:assert/strict';
import {planeHit,sceneHit,sceneCamera,scenePose,cameraBasis,textFitDistance} from '../static/galaxy/scene-geometry.mjs';
import {PlanetDirector} from '../static/galaxy/planet-controls.mjs';
const visual={radius:1.5,galaxy_radius:5.2,camera_distance:7.8,close_distance:3.6,galaxy_distance:10.4,galaxy_close_distance:9.6,text_distance:7.8,text_close_distance:5.8,text_depth:.35,text_flow:.06,text_viewport_fraction:.9,field_of_view:50,camera_yaw_deg:0,camera_pitch_deg:25,camera_roll_deg:-12};
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
const options={stale_ms:400,interaction:{zoom_dwell_ms:1500,camera_spring_hz:.7,zoom_spring_hz:.65,yaw_range_deg:35,pitch_range_deg:35,galaxy_rotation_range_deg:18,galaxy_pitch_range_deg:8,galaxy_min_pitch_deg:20,galaxy_zoom_core_ratio:.35,text_yaw_range_deg:4,text_pitch_range_deg:3,gaze_fade_s:.5}};
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

const i=options.interaction;
assert.equal(scenePose('planet',{...visual,camera_pitch_deg:85},i,1.59).pitch,85); // Static planet config preserves its original range.
assert.ok(Math.abs(scenePose('planet',visual,i,1.59,{pitch:0,yaw:0,zoom:1}).distance-3.6)<1e-12);
assert.equal(sceneHit('galaxy',[1,5,0],[0,-1,0],visual,1,null,i).hit,true);
assert.equal(sceneHit('galaxy',[2,5,0],[0,-1,0],visual,1,null,i).hit,false);
assert.equal(sceneHit('galaxy',[2,5,0],[0,-1,0],visual,2,null,i).hit,true);
const pose=scenePose('galaxy',visual,i,1.59,{yaw:18,pitch:-8,zoom:1});
assert.equal(pose.pitch,20);assert.equal(pose.yaw,0);assert.equal(pose.rotation,18);assert.equal(pose.distance,9.6);
assert.equal(scenePose('planet',visual,i,1.59,{yaw:35,pitch:-35,zoom:1}).pitch,-10); // Planet unchanged.
// Project all eight glyph corners, with deep and tall glyphs / narrow viewports /
// Retina-independent logical aspect / hand scaling. Even full zoom must fit 90%.
for(const aspect of [.5,1,1512/950,16/9,3])for(const scale of [.65,1,1.5])for(const yaw of [-4,0,4])for(const pitch of [-3,0,3])for(const b of [bounds,[-3.6,3.6,-3.5,3.5]]){
 const control={yaw,pitch,zoom:1},pose=scenePose('text',visual,i,aspect,control,scale,b),axes=cameraBasis(yaw,pitch);
 assert.ok(pose.distance>=textFitDistance(b,.235,50,aspect,yaw,pitch,scale,.9)-1e-10);
 for(const x of [b[0],b[1]])for(const y of [b[2],b[3]])for(const z of [-.235,.235]){
  const point=[x*scale,y*scale,z*scale],dot=v=>v.reduce((s,t,n)=>s+t*point[n],0),depth=pose.distance-dot(axes.normal);
  assert.ok(Math.abs(dot(axes.right)/depth/Math.tan(25*Math.PI/180)/aspect)<=.9+1e-10);
  assert.ok(Math.abs(dot(axes.up)/depth/Math.tan(25*Math.PI/180))<=.9+1e-10);
 }
}
for(const xy of [[0,0],[1000,0],[0,800],[1000,800],[500,0],[500,800]]){
 const d=new PlanetDirector(options);let c;
 for(let ms=0;ms<=3000;ms+=50)c=d.update({intro:0,gaze_valid:1,capture_us:ms*1000,xy},.05,[1000,800],false,1,'galaxy');
 const p=scenePose('galaxy',visual,i,1.25,c);
 assert.ok(p.pitch>=20&&p.pitch<=33);assert.equal(p.yaw,0);assert.ok(Math.abs(p.rotation)<=18);
 const td=new PlanetDirector(options);let tc;
 for(let ms=0;ms<=3000;ms+=50)tc=td.update({intro:0,gaze_valid:1,capture_us:ms*1000,xy},.05,[1000,800],false,1,'text');
 assert.ok(Math.abs(tc.yaw)<=4&&Math.abs(tc.pitch)<=3);
}
console.log('core-only zoom, disk-normal gaze rotation, >=20deg elevation and perspective-safe text fitting passed');
for(const aspect of [.5,1,1.59,2.5]){
 const fv={...visual,galaxy_face_fill:.9},face=scenePose('galaxy',fv,i,aspect,null,1,null,'face');
 assert.equal(face.pitch,90);assert.equal(face.roll,0);assert.equal(face.yaw,0);
 const vertical=fv.galaxy_radius/(face.distance*Math.tan(fv.field_of_view*Math.PI/360));
 assert.ok(vertical<=.9+1e-12&&vertical/aspect<=.9+1e-12);
}
assert.equal(scenePose('planet',visual,i,1.59,null,1,null,'face').pitch,visual.camera_pitch_deg);
console.log('galaxy-only face-on framing keeps its disk inside the logical viewport');
