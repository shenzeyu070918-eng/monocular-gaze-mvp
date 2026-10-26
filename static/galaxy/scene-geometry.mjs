// Pure world-space hit tests. Viewport/global coordinate mapping stays in Python.
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
export function planeHit(origin,direction,axis){
 if(Math.abs(direction[axis])<1e-8)return null;
 const t=-origin[axis]/direction[axis];
 return t<0?null:origin.map((v,i)=>v+direction[i]*t);
}
function sphereHit(origin,direction,radius){
 const a=dot(direction,direction),b=dot(origin,direction),c=dot(origin,origin)-radius*radius,disc=b*b-a*c;
 return a>0&&disc>=0&&(-b+Math.sqrt(disc))/a>=0;
}
export function sceneHit(scene,origin,direction,visual,scale=1,textBounds=null,interaction={}){
 if(!['planet','galaxy','text'].includes(scene))throw new Error('Unknown scene');
 const well=planeHit(origin,direction,scene==='text'?2:1);
 let hit=false;
 if(scene==='planet')hit=sphereHit(origin,direction,visual.radius*scale);
 else if(well&&scene==='galaxy')hit=well[0]**2+well[2]**2<=(visual.galaxy_radius*scale*(interaction.galaxy_zoom_core_ratio??1))**2;
 else if(well&&textBounds)hit=well[0]>=textBounds[0]*scale&&well[0]<=textBounds[1]*scale&&well[1]>=textBounds[2]*scale&&well[1]<=textBounds[3]*scale;
 return {hit,well};
}
export function sceneCamera(scene,visual){
 if(scene==='planet')return {far:visual.camera_distance,near:visual.close_distance,pitch:visual.camera_pitch_deg,roll:visual.camera_roll_deg};
 if(scene==='galaxy')return {far:visual.galaxy_distance,near:visual.galaxy_close_distance,pitch:visual.camera_pitch_deg,roll:visual.camera_roll_deg};
 if(scene==='text')return {far:visual.text_distance,near:visual.text_close_distance,pitch:0,roll:0};
 throw new Error('Unknown scene');
}

// Basis used by a look-at-origin camera, before roll (text has zero roll).
export function cameraBasis(yawDeg,pitchDeg){
 const y=yawDeg*Math.PI/180,p=pitchDeg*Math.PI/180;
 return {right:[Math.cos(y),0,-Math.sin(y)],up:[-Math.sin(p)*Math.sin(y),Math.cos(p),-Math.sin(p)*Math.cos(y)],normal:[Math.cos(p)*Math.sin(y),Math.sin(p),Math.cos(p)*Math.cos(y)]};
}
export function textFitDistance(bounds,depth,fov,aspect,yaw,pitch,scale,fraction){
 const {right,up,normal}=cameraBasis(yaw,pitch),v=Math.tan(fov*Math.PI/360)*fraction,h=v*aspect;
 let distance=0;
 for(const x of [bounds[0],bounds[1]])for(const y of [bounds[2],bounds[3]])for(const z of [-depth,depth]){
  const point=[x*scale,y*scale,z*scale],towards=dot(point,normal);
  distance=Math.max(distance,towards+Math.abs(dot(point,right))/h,towards+Math.abs(dot(point,up))/v);
 }
 return distance;
}
export function scenePose(scene,visual,interaction,aspect,control=null,scale=1,bounds=null,view='oblique'){
 const camera=sceneCamera(scene,visual),c=control??{pitch:0,yaw:0,zoom:0};
 let pitch=view==='top'?75:camera.pitch,yaw=scene==='text'?0:visual.camera_yaw_deg,near=camera.near,far=camera.far;
 if(control||scene!=='planet')pitch=Math.max(-80,Math.min(80,pitch+c.pitch));
 if(scene==='galaxy')pitch=Math.max(interaction.galaxy_min_pitch_deg,pitch);
 else yaw+=c.yaw;
 if(scene==='text'&&bounds){
  near=Math.max(near,textFitDistance(bounds,visual.text_depth/2+visual.text_flow,visual.field_of_view,aspect,yaw,pitch,scale,visual.text_viewport_fraction));
  // Maintain a gentle zoom even when a narrow viewport needs a larger distance.
  far=Math.max(far,near*camera.far/camera.near);
 }
 if(scene==='galaxy'&&view==='face')return {pitch:90,yaw:0,roll:0,distance:visual.galaxy_radius/(Math.tan(visual.field_of_view*Math.PI/360)*Math.min(1,aspect)*visual.galaxy_face_fill),near,far,rotation:c.yaw};
 const start=view==='close'?near:far;
 return {pitch,yaw,roll:camera.roll,distance:start+(near-start)*c.zoom,near,far,rotation:scene==='galaxy'?c.yaw:0};
}
