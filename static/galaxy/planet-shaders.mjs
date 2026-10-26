import {galaxyMotionGLSL} from './galaxy-motion.mjs';
// Analytical GPU orbits; weak gaze forces operate only on display particles.
export const particleVertex=`
attribute vec4 aSeed,aGalaxy;attribute vec3 aLetter;attribute float aFlow;
uniform float time,intro,kind,radius,innerRatio,orbitSpeed,planetSpin,swirlAmplitude,swirlSpeed;
uniform float wanderFraction,wanderDistance,pointSize,pointMin,pointMax,pixelRatio,referenceDistance,absorption,intensity,galaxySpin,coreIntensity,textWidth;
uniform float galaxyDrift,galaxyDriftSpeed,galaxyPitchCot;
uniform float chargeLevel,chargePhase,chargeCoreRadius,chargeCoreLight,chargeCoreSpin,chargeCoreContract,chargeRingSpeed;
uniform vec3 warmA,warmB,warmC,coolA,coolB,coolC,well;
uniform float letterWeights[80],letterScale,letterLift,letterBrightness,spotSize,spotTwinkle,spotSpeed,galaxySwirl;
uniform float wellRadius,eyeAlpha,wellAttraction,wellBrightness,sceneFade;
uniform float scatter,handScale,scatterDistance,scatterHeight,scatterScale,sceneRotation,textCurl,textStagger;
uniform float outerRatio,ringLightPower,ringOuterLight,brightFraction,brightSize,brightIntensity,brightHalo,twinkleSpeed,rimStrength,rimPower;
varying vec3 particleColor;varying float opacity,halo;
vec3 palette(vec3 a,vec3 b,vec3 c,float t){return t<.5?mix(a,b,t*2.):mix(b,c,(t-.5)*2.);}
vec3 turnY(vec3 p,float a){float c=cos(a),s=sin(a);return vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);}
${galaxyMotionGLSL}
void main(){
 vec3 p=position;float r=length(p.xz);float phase=aSeed.x*6.2831853;float tail=max(0.,(aSeed.y-(1.-brightFraction))/max(brightFraction,.00001));
 float shimmer=1.-(.08+tail*.17)*(1.-sin(time*twinkleSpeed+phase*9.));halo=brightHalo*tail;
 float pointBoost=1.;vec3 color;float light=.55+.8*pow(aSeed.y,2.2)+(brightIntensity-1.)*tail;
 if(kind<.5){
  p=turnY(p,time*planetSpin);
  p+=swirlAmplitude*radius*vec3(sin(p.y*1.6+time*swirlSpeed+phase),sin(p.z*1.4-time*swirlSpeed),cos(p.x*1.3+time*swirlSpeed+phase));
  color=palette(warmA,warmB,warmC,aSeed.z);
  light*=.8+.2*clamp(p.y/radius+.5,0.,1.);
 }else if(kind<1.5){
  float speed=orbitSpeed*pow(radius*innerRatio/max(r,.001),1.5);
  p=turnY(p,time*speed+chargePhase*speed*chargeRingSpeed);
  p.y+=sin(atan(p.z,p.x)*3.+time*.35)*radius*.009;
  if(aSeed.w<wanderFraction){float drift=pow(max(0.,sin(fract(time*.045+aSeed.z)*3.14159265)),6.);
   p.xz*=1.+wanderDistance*radius*drift/max(r,.01);p.y+=drift*radius*.17*sin(phase);}
  float radial=clamp((r/radius-innerRatio)/(outerRatio-innerRatio),0.,1.);
  color=palette(coolC,coolB,coolA,pow(radial,.6));
  light*=ringOuterLight+(1.-ringOuterLight)*pow(1.-radial,ringLightPower);
 }else if(kind<2.5){
  p=galaxyMotion(p,aSeed,time,galaxySpin,galaxyDrift,galaxyDriftSpeed,galaxyPitchCot,sceneRotation);
  p.y+=sin(time*.3+phase)*radius*.017;
  // aFlow==3 tags the bulge. Never differentially rotate or contract the arms.
  if(aFlow>2.5&&aFlow<3.5){p=turnY(p,chargePhase*galaxySpin*chargeCoreSpin);p*=1.-chargeLevel*chargeCoreContract;}
  float coreMask=1.-smoothstep(0.,chargeCoreRadius,r);
  color=aGalaxy.rgb;light*=aGalaxy.a*(1.+chargeLevel*chargeCoreLight*coreMask);
 }else{
  // aFlow is bounded by distance INSIDE the filled glyph, keeping its body full.
  p.xy+=vec2(sin(time*.7+phase),cos(time*.63+phase*1.7))*aFlow*.65;
  p.z+=sin(time*.8+phase)*aFlow;
  float gradient=clamp(p.x/textWidth+.5,0.,1.);
  color=mix(mix(coolB,vec3(.5,.27,.7),gradient),warmB,aSeed.z*.25);
  float focus=letterWeights[int(aLetter.x)];
  p.xy=aLetter.yz+(p.xy-aLetter.yz)*(1.+letterScale*focus);p.z+=letterLift*focus;light*=1.+letterBrightness*focus;
 }
 // The same weak force follows the ray/plane hit in ring, galaxy and text.
 if(kind>.5){
  vec3 delta=well-p;float wellFalloff=exp(-dot(delta,delta)/max(.001,2.*wellRadius*wellRadius))*eyeAlpha;
  if(kind>1.5){wellFalloff=(1.-smoothstep(.25,1.,length(delta)/max(.001,wellRadius)))*eyeAlpha;
   pointBoost=1.+spotSize*wellFalloff;light*=1.+spotTwinkle*wellFalloff*sin(time*spotSpeed+phase);}
  p+=delta*wellAttraction*wellFalloff;light*=1.+wellBrightness*wellFalloff;
 }
 // Prescribed restoring shape stays on the GPU. Relaxing scatter returns each
 // seed to its CURRENT orbit, instead of freezing or snapping to an old frame.
 if(kind>.5&&kind<2.5){
  vec2 radial=normalize(p.xz+vec2(.0001)),tangent=vec2(-radial.y,radial.x);
  p.xz+=(radial*(.3+aSeed.w*.7)+tangent*(aSeed.x-.5))*scatter*scatterDistance*scatterScale;
  p.y+=sin(phase*3.)*scatter*scatterHeight*scatterScale;
  if(kind>1.5)p=turnY(p,scatter*galaxySwirl*(.6+.4*sin(time*.8+phase)));
 }
 if(kind>2.5){
  // Uniform per-seed directions are independent of the particle's location.
  // A seeded smoothstep phase staggers both expansion and eased return. There
  // is only ONE position per particle, recomputed from the current flowing glyph.
  float cloud=smoothstep(aSeed.w*textStagger,aSeed.w*textStagger+1.-textStagger,scatter);
  float z=aSeed.z*2.-1.,xy=sqrt(max(0.,1.-z*z));
  vec3 direction=vec3(cos(phase)*xy,sin(phase)*xy,z);
  // Curl of (sin(y),sin(z),sin(x)): divergence-free and slowly evolving.
  vec3 curl=-cos(p.zxy+time*.6+aSeed.wxy*6.2831853);
  vec3 offset=(direction+textCurl*curl)/(1.+textCurl);
  p.xy+=offset.xy*cloud*scatterDistance*scatterScale;
  p.z+=offset.z*cloud*scatterHeight*scatterScale;
 }
 float displayScale=handScale;p*=displayScale;
 float assembly=intro*intro*(3.-2.*intro);
 vec3 chaos=(aSeed.xyz-.5)*vec3(22.,14.,16.);
 p=mix(chaos,p,assembly);
 vec4 world=modelMatrix*vec4(p,1.);vec4 mv=viewMatrix*world;
 gl_Position=projectionMatrix*mv;
 float depth=max(.1,-mv.z),raw=pointSize*pointBoost*(referenceDistance/depth)*(.45+.5*sqrt(aSeed.y)+(brightSize-1.)*tail)*(1.+halo*2.);
 gl_PointSize=clamp(raw,pointMin,pointMax)*pixelRatio;
 opacity=min(1.,raw*raw)*(.25+.75*assembly)*sceneFade;
 // Analytic transmittance through the volume: back-ring particles are dimmed,
 // while the volume stays made of particles (there is no opaque sphere mesh).
 if(kind<1.5){
  vec3 ray=normalize(world.xyz-cameraPosition);float distanceToPoint=length(world.xyz-cameraPosition);
  float effectiveRadius=radius*displayScale;
  float b=dot(cameraPosition,ray),disc=b*b-dot(cameraPosition,cameraPosition)+effectiveRadius*effectiveRadius;
  if(disc>0.){float root=sqrt(disc),nearT=-b-root,farT=-b+root;
   float travel=max(0.,min(distanceToPoint,farT)-max(0.,nearT));opacity*=exp(-absorption*travel/effectiveRadius);
   if(kind<.5)light*=1.+rimStrength*pow(1.-clamp(disc/(effectiveRadius*effectiveRadius),0.,1.),rimPower);}
 }
 float depthLight=clamp(pow(referenceDistance/depth,.55),.18,1.1);
 particleColor=color*light*shimmer*intensity*depthLight;
}`;

export const particleFragment=`
varying vec3 particleColor;varying float opacity,halo;
void main(){float r=length(gl_PointCoord-.5)*2.;if(r>1.)discard;
 float edge=1.-smoothstep(.2/(1.+halo*2.),.85/(1.+halo*2.),r);edge+=halo*exp(-r*r*5.);gl_FragColor=vec4(particleColor,edge*opacity*.64);}
`;

export const coreVertex=`uniform float size,pixelRatio,fade;varying float alpha;void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);gl_PointSize=size*pixelRatio;alpha=fade;}`;
export const coreFragment=`uniform float brightness;varying float alpha;void main(){vec2 p=gl_PointCoord-.5;float r=length(p);
 float core=exp(-r*r*80.);float halo=exp(-r*r*55.)*.11;
 float rays=exp(-min(abs(p.x),abs(p.y))*180.)*exp(-r*14.)*.26;
 gl_FragColor=vec4(vec3(.85,.95,1.12)*(core+halo+rays)*2.3*brightness,alpha);}`;
