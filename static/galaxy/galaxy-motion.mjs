// One shared GLSL function is used by the actual particle shader and GPU checks.
export const galaxyMotionGLSL=`
vec3 galaxyMotion(vec3 p,vec4 seed,float t,float spin,float drift,float driftSpeed,float pitchCot,float rotation){
 float scale=1.+drift*sin(t*driftSpeed+seed.x*6.2831853);
 p.xz*=scale;
 // Bounded displacement ALONG the log arm: d(theta)=cot(pitch)*d(log r).
 // The density-wave pattern rotates rigidly, including accelerated motionTime.
 return turnY(p,t*spin+rotation-log(scale)*pitchCot);
}`;
export function galaxyPosition(p,seed,time,o,rotation=0){
 const s=1+o.galaxy_drift_fraction*Math.sin(time*o.galaxy_drift_speed+seed[0]*2*Math.PI),a=time*o.galaxy_spin+rotation-Math.log(s)/Math.tan(o.galaxy_pitch_deg*Math.PI/180),c=Math.cos(a),sn=Math.sin(a);
 return [s*(c*p[0]+sn*p[2]),p[1],s*(-sn*p[0]+c*p[2])];
}
