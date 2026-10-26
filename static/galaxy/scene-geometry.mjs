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
export function sceneHit(scene,origin,direction,visual,scale=1,textBounds=null){
 if(!['planet','galaxy','text'].includes(scene))throw new Error('Unknown scene');
 const well=planeHit(origin,direction,scene==='text'?2:1);
 let hit=false;
 if(scene==='planet')hit=sphereHit(origin,direction,visual.radius*scale);
 else if(well&&scene==='galaxy')hit=well[0]**2+well[2]**2<=(visual.galaxy_radius*scale)**2;
 else if(well&&textBounds)hit=well[0]>=textBounds[0]*scale&&well[0]<=textBounds[1]*scale&&well[1]>=textBounds[2]*scale&&well[1]<=textBounds[3]*scale;
 return {hit,well};
}
export function sceneCamera(scene,visual){
 if(scene==='planet')return {far:visual.camera_distance,near:visual.close_distance,pitch:visual.camera_pitch_deg,roll:visual.camera_roll_deg};
 if(scene==='galaxy')return {far:visual.galaxy_distance,near:visual.galaxy_close_distance,pitch:visual.camera_pitch_deg,roll:visual.camera_roll_deg};
 if(scene==='text')return {far:visual.text_distance,near:visual.text_close_distance,pitch:0,roll:0};
 throw new Error('Unknown scene');
}
