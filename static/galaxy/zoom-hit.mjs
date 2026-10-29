// Graphics projection only. All input positions/radii are viewport logical pixels.
export function projectedRadius(radius,depth,fov,height){return Math.max(0,radius)*height/(2*Math.max(.001,depth)*Math.tan(fov*Math.PI/360));}
export function coreTarget(scene,xy,center,worldRadiusPx,errorRadii,size,i,sphereHit=false,sphereExit=false){
 const radius=scene==='planet'?worldRadiusPx:Math.max(worldRadiusPx*i.zoom_core_scale,Math.max(...errorRadii)*i.zoom_error_scale,size[1]*i.zoom_viewport_ratio);
 const distance=xy?Math.hypot(xy[0]-center[0],xy[1]-center[1]):Infinity;
 return {center,radius,inside:scene==='planet'?sphereHit:distance<=radius,retain:scene==='planet'?sphereExit:distance<=radius*i.zoom_exit_ratio};
}
