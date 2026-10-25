import * as THREE from 'three';
import {GPUComputationRenderer} from 'three/addons/misc/GPUComputationRenderer.js';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {makeShapes} from './shapes.mjs';
import {Spring2D,handPhysics} from './logic.mjs';

const velocityShader=`
uniform float delta,time,restoreForce,dragForce,explodeForce,collapse,expansion,scaleFactor,intro;
uniform sampler2D targetA,targetB;uniform float morph;
void main(){vec2 uv=gl_FragCoord.xy/resolution.xy;
 vec3 p=texture2D(texturePosition,uv).xyz,v=texture2D(textureVelocity,uv).xyz;
 if(intro<1.){gl_FragColor=vec4(0.);return;}
 vec3 t=mix(texture2D(targetA,uv).xyz,texture2D(targetB,uv).xyz,morph)*scaleFactor*(1.-.97*collapse);
 vec3 radial=normalize(p+vec3(.001));
 vec3 curl=vec3(sin(p.y*3.+time),cos(p.x*2.-time),sin(p.z*2.+time))*.12;
 vec3 a=(t-p)*restoreForce+radial*expansion*explodeForce+curl-v*dragForce;
 v+=a*delta;v=clamp(v,vec3(-12.),vec3(12.));gl_FragColor=vec4(v,1.);
}`;
const positionShader=`
uniform float delta,intro,attractionForce,radiusWorld;uniform sampler2D chaos,targetA;
void main(){vec2 uv=gl_FragCoord.xy/resolution.xy;vec3 p=texture2D(texturePosition,uv).xyz;
 if(intro<1.){float t=intro*intro*(3.-2.*intro);p=mix(texture2D(chaos,uv).xyz,texture2D(targetA,uv).xyz,t);}
 else {vec3 v=texture2D(textureVelocity,uv).xyz;p+=v*delta;
 float f=max(0.,1.-length(p)/max(radiusWorld,.01));p-=p*f*attractionForce*delta;}
 gl_FragColor=vec4(p,1.);
}`;
const starVertex=`
attribute vec2 reference;attribute float seed;uniform sampler2D positions;
uniform float size,pixelRatio,eyeAlpha,gazeBrightness;uniform vec2 gaze,radius,viewport;
varying vec3 tint;varying float brightness;
void main(){vec3 p=texture2D(positions,reference).xyz;vec4 mv=modelViewMatrix*vec4(p,1.);gl_Position=projectionMatrix*mv;
 vec2 screen=(gl_Position.xy/gl_Position.w*.5+.5)*viewport;screen.y=viewport.y-screen.y;
 float nearEye=max(0.,1.-length((screen-gaze)/max(radius,vec2(1.))));
 brightness=1.+eyeAlpha*nearEye*gazeBrightness;tint=mix(vec3(.28,.55,1.),vec3(1.,.78,.48),pow(seed,6.));
 gl_PointSize=clamp(size*pixelRatio*(8./max(1.,-mv.z))*(.7+seed),1.,14.);
}`;
const starFragment=`varying vec3 tint;varying float brightness;uniform float intensity;void main(){float r=length(gl_PointCoord-vec2(.5))*2.;if(r>1.)discard;
 float a=pow(1.-r,2.);gl_FragColor=vec4(tint*brightness*intensity,a*.85);}`;

export class GalaxyRenderer {
 constructor(canvas,o,quality){
  this.canvas=canvas;this.o=o;this.quality=quality;this.spring=new Spring2D();this.pattern=0;this.previous=0;this.morph=1;this.eyeAlpha=0;this.spin=0;
  const context=canvas.getContext('webgl2',{alpha:false,antialias:false,powerPreference:'high-performance'});
  if(!context)throw new Error('WebGL2不可用');
  if(!context.getExtension('EXT_color_buffer_float'))throw new Error('GPU浮点渲染不可用');
  this.renderer=new THREE.WebGLRenderer({canvas,context,antialias:false});this.renderer.setClearColor(0x000000);
  this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.scene=new THREE.Scene();
  this.camera=new THREE.PerspectiveCamera(o.field_of_view,1,.1,100);this.camera.position.z=o.camera_z;
  this.composer=new EffectComposer(this.renderer);this.composer.addPass(new RenderPass(this.scene,this.camera));
  this.bloom=new UnrealBloomPass(new THREE.Vector2(1,1),o.bloom_strength,o.bloom_radius,o.bloom_threshold);
  this.composer.addPass(this.bloom);this.composer.addPass(new OutputPass());
  this.group=new THREE.Group();this.scene.add(this.group);this.background();this.build(quality.particles);this.resize();
 }
 background(){
  const o=this.o,positions=new Float32Array(o.star_count*3),colors=new Float32Array(o.star_count*3);
  let s=771;const rand=()=>{s=(Math.imul(s,1664525)+1013904223)>>>0;return s/4294967296;};
  for(let i=0;i<o.star_count;i++){positions.set([(rand()-.5)*50,(rand()-.5)*28,-4-rand()*18],i*3);const b=.2+rand()*.45;colors.set([b*.6,b*.8,b],i*3);}
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(positions,3));g.setAttribute('color',new THREE.BufferAttribute(colors,3));
  this.stars=new THREE.Points(g,new THREE.PointsMaterial({size:.025,vertexColors:true,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending}));this.scene.add(this.stars);
  const material=new THREE.ShaderMaterial({depthWrite:false,depthTest:false,transparent:true,uniforms:{time:{value:0}},
   vertexShader:'varying vec2 uv0;void main(){uv0=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
   fragmentShader:'varying vec2 uv0;uniform float time;float n(vec2 p){return sin(p.x*4.+time*.03)*cos(p.y*3.-time*.02)*.5+.5;}void main(){vec2 p=(uv0-.5)*4.;float cloud=pow(n(p)+n(p*2.1)*.4,3.)*.07;float fade=exp(-dot(p,p)*.25);gl_FragColor=vec4(mix(vec3(.15,.25,.7),vec3(.5,.13,.65),n(p+1.))*cloud*fade,1.);}'});
  this.nebula=new THREE.Mesh(new THREE.PlaneGeometry(45,28),material);this.nebula.position.z=-10;this.nebula.renderOrder=-2;this.scene.add(this.nebula);
 }
 build(count){
  if(this.compute){this.group.remove(this.points);this.points.geometry.dispose();this.points.material.dispose();this.compute.dispose();this.shapes.forEach(t=>t.dispose());this.chaos.dispose();}
  this.count=count;this.tw=250;this.th=count/250;
  const compute=this.compute=new GPUComputationRenderer(this.tw,this.th,this.renderer);
  const position=compute.createTexture(),velocity=compute.createTexture();
  for(let i=0;i<count;i++)position.image.data.set([(Math.random()-.5)*20,(Math.random()-.5)*14,(Math.random()-.5)*12,1],i*4);
  this.chaos=position.clone();this.chaos.needsUpdate=true;
  const arrays=makeShapes(THREE,count,this.o.text);this.shapes=arrays.map(a=>{const t=compute.createTexture();t.image.data.set(a);return t;});
  this.pos=compute.addVariable('texturePosition',positionShader,position);this.vel=compute.addVariable('textureVelocity',velocityShader,velocity);
  compute.setVariableDependencies(this.pos,[this.pos,this.vel]);compute.setVariableDependencies(this.vel,[this.pos,this.vel]);
  this.pos.material.uniforms={delta:{value:0},intro:{value:0},chaos:{value:this.chaos},targetA:{value:this.shapes[this.pattern]},attractionForce:{value:0},radiusWorld:{value:1}};
  this.vel.material.uniforms={delta:{value:0},time:{value:0},targetA:{value:this.shapes[this.previous]},targetB:{value:this.shapes[this.pattern]},morph:{value:1},
   restoreForce:{value:this.o.restore},dragForce:{value:this.o.drag},explodeForce:{value:this.o.explode},collapse:{value:0},expansion:{value:0},scaleFactor:{value:1},intro:{value:0}};
  const error=compute.init();if(error)throw new Error(error);
  const reference=new Float32Array(count*2),seed=new Float32Array(count);
  for(let i=0;i<count;i++){reference[i*2]=(i%this.tw+.5)/this.tw;reference[i*2+1]=(Math.floor(i/this.tw)+.5)/this.th;seed[i]=(i*0.61803398875)%1;}
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(count*3),3));g.setAttribute('reference',new THREE.BufferAttribute(reference,2));g.setAttribute('seed',new THREE.BufferAttribute(seed,1));
  this.material=new THREE.ShaderMaterial({vertexShader:starVertex,fragmentShader:starFragment,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,
   uniforms:{positions:{value:null},size:{value:this.o.point_size},pixelRatio:{value:1},eyeAlpha:{value:0},gazeBrightness:{value:this.o.gaze_brightness},intensity:{value:this.o.particle_brightness},gaze:{value:new THREE.Vector2()},radius:{value:new THREE.Vector2(1,1)},viewport:{value:new THREE.Vector2(1,1)}}});
  this.points=new THREE.Points(g,this.material);this.points.frustumCulled=false;this.group.add(this.points);
 }
 resize(){
  this.renderer.setPixelRatio(Math.min(devicePixelRatio,this.quality.ratio));this.renderer.setSize(innerWidth,innerHeight,false);this.composer.setPixelRatio(this.renderer.getPixelRatio());this.composer.setSize(innerWidth,innerHeight);
  this.camera.aspect=innerWidth/innerHeight;this.camera.updateProjectionMatrix();
  this.material.uniforms.pixelRatio.value=this.renderer.getPixelRatio();this.material.uniforms.viewport.value.set(innerWidth,innerHeight);
 }
 draw(frame,dt,time,intro){
  if(this.count!==this.quality.particles){this.build(this.quality.particles);this.resize();}
  const interactive=intro>=1,valid=interactive && frame && frame.gaze_valid;
  const target=valid?[frame.xy[0]/innerWidth*2-1,1-frame.xy[1]/innerHeight*2]:this.spring.p;
  const center=this.spring.step(target,dt,this.o.spring_hz),half=Math.tan(THREE.MathUtils.degToRad(this.o.field_of_view/2))*this.o.camera_z;
  this.group.position.set(center[0]*half*this.camera.aspect,center[1]*half,0);
  this.eyeAlpha+=(Number(!!valid)-this.eyeAlpha)*(1-Math.exp(-dt*6));
  const h=interactive?handPhysics(frame):handPhysics(null);
  this.spin+=dt*.09;
  // Flat text/heart must remain legible, not turn edge-on after a long demo.
  // Globe rotates about Y; the face-on spiral rotates in its own XY plane.
  const turn=1-Math.exp(-dt*4),autoY=this.pattern===3?this.spin:0,autoZ=this.pattern===0?this.spin*.5:0;
  this.group.rotation.y+=Math.atan2(Math.sin(autoY+h.ry*this.o.rotation-this.group.rotation.y),Math.cos(autoY+h.ry*this.o.rotation-this.group.rotation.y))*turn;
  this.group.rotation.z+=Math.atan2(Math.sin(autoZ-this.group.rotation.z),Math.cos(autoZ-this.group.rotation.z))*turn;
  this.group.rotation.x+=(h.rx*.7+.12-this.group.rotation.x)*turn;
  this.stars.position.x=-center[0]*.4;this.stars.position.y=-center[1]*.25;this.nebula.material.uniforms.time.value=time;
  if(interactive && frame && frame.pattern!==this.pattern){this.previous=this.pattern;this.pattern=frame.pattern;this.morph=0;}
  this.morph=Math.min(1,this.morph+dt/this.o.transition_s);
  const p=this.pos.material.uniforms,v=this.vel.material.uniforms;
  p.delta.value=v.delta.value=dt;p.intro.value=v.intro.value=intro;v.time.value=time;
  p.targetA.value=this.shapes[0];v.targetA.value=this.shapes[this.previous];v.targetB.value=this.shapes[this.pattern];v.morph.value=this.morph;
  v.collapse.value=h.collapse;v.expansion.value=h.explode;v.scaleFactor.value=h.scale;
  p.attractionForce.value=this.o.attraction*this.eyeAlpha;p.radiusWorld.value=frame?frame.radius[1]/innerHeight*half*2:1;
  this.compute.compute();this.material.uniforms.positions.value=this.compute.getCurrentRenderTarget(this.pos).texture;
  this.material.uniforms.eyeAlpha.value=this.eyeAlpha;
  const exposureA=this.previous===2?this.o.text_brightness:1,exposureB=this.pattern===2?this.o.text_brightness:1;
  this.material.uniforms.intensity.value=this.o.particle_brightness*(exposureA+(exposureB-exposureA)*this.morph);
  if(frame){this.material.uniforms.gaze.value.set(...frame.xy);this.material.uniforms.radius.value.set(...frame.radius);}
  this.composer.render();
 }
 close(){this.compute?.dispose();this.shapes?.forEach(t=>t.dispose());this.chaos?.dispose();this.scene.traverse(o=>{o.geometry?.dispose();if(o.material)o.material.dispose();});this.composer.dispose();this.bloom.dispose();this.renderer.dispose();}
}
