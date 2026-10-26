import * as THREE from 'three';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {planetField,ringField,galaxyField,textField,randomSource,particleBudget} from './particle-fields.mjs';
import {sceneHit,scenePose} from './scene-geometry.mjs';
import {AssemblyTransition} from './assembly.mjs';
import {BurstEnvelope} from './burst.mjs';
import {LetterFocus,letterHit,MAX_LETTERS} from './letter-focus.mjs';
import {PlanetDirector,HandDynamics} from './planet-controls.mjs';
import {particleVertex,particleFragment,coreVertex,coreFragment} from './planet-shaders.mjs';

function textMask(text){
 const canvas=document.createElement('canvas');canvas.width=1024;canvas.height=384;
 const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.font='900 240px Arial, sans-serif';
 ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='white';ctx.fillText(text,512,192,960);
 const data=ctx.getImageData(0,0,1024,384).data,mask=new Uint8Array(1024*384);
 for(let i=0;i<mask.length;i++)mask[i]=Number(data[i*4+3]>128);
 const chars=typeof Intl.Segmenter==='function'?[...new Intl.Segmenter(undefined,{granularity:'grapheme'}).segment(text)].map(v=>v.segment):Array.from(text);
 const total=ctx.measureText(text).width,fit=Math.min(1,960/total),left=512-total*fit/2,ranges=[];let prefix='';
 for(const char of chars){const start=ctx.measureText(prefix).width;prefix+=char;ranges.push([left+start*fit,left+ctx.measureText(prefix).width*fit]);}
 return {mask,width:1024,height:384,ranges};
}

export class PlanetRenderer{
 constructor(canvas,options,quality=null){
  this.quality=quality;this.modelScale=1;this.raycaster=new THREE.Raycaster();this.pointer=new THREE.Vector2();this.depthPoint=new THREE.Vector3();this.origin=[0,0,0];this.direction=[0,0,0];
  this.assembly=new AssemblyTransition(options);this.burst=new BurstEnvelope(options);this.clockMs=0;this.emptyLetters=new Float32Array(MAX_LETTERS);
  this.options=options;this.o=options.visual;this.text=options.text;this.director=null;this.hands=null;this.motionTime=0;this.fade=1;this.pending=null;this.pixelRatio=Math.min(devicePixelRatio,options.pixel_ratio);this.items=[];this.sceneName='';
  const context=canvas.getContext('webgl2',{alpha:false,antialias:false,powerPreference:'high-performance'});
  if(!context)throw new Error('需要WebGL2，请使用支持硬件加速的Chrome');
  if(!context.getExtension('EXT_color_buffer_float'))throw new Error('需要浮点颜色缓冲，请启用浏览器硬件加速');
  this.renderer=new THREE.WebGLRenderer({canvas,context,antialias:false});this.renderer.setClearColor(0x000000);
  this.renderer.debug.onShaderError=()=>{throw new Error('GPU着色器编译失败，请更换浏览器或切回Qt备用');};
  this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=this.o.exposure;
  this.scene=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(this.o.field_of_view,1,.05,150);
  this.composer=new EffectComposer(this.renderer);this.composer.addPass(new RenderPass(this.scene,this.camera));
  this.bloom=new UnrealBloomPass(new THREE.Vector2(1,1),this.o.bloom_strength,this.o.bloom_radius,this.o.bloom_threshold);
  this.composer.addPass(this.bloom);this.composer.addPass(new OutputPass());
  this.background();this.makeCore();this.setScene('planet');this.resize();this.placeCamera();
 }
 background(){
  const rand=randomSource(this.o.seed+9),n=this.o.background_stars,positions=new Float32Array(n*3),colors=new Float32Array(n*3);
  for(let i=0;i<n;i++){const a=rand()*Math.PI*2,z=rand()*2-1,r=Math.sqrt(1-z*z);positions.set([Math.cos(a)*r*60,z*60,Math.sin(a)*r*60],i*3);const b=.05+rand()*.1;colors.set([b*.6,b*.8,b],i*3);}
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(positions,3));g.setAttribute('color',new THREE.BufferAttribute(colors,3));
  this.stars=new THREE.Points(g,new THREE.PointsMaterial({size:.06,vertexColors:true,depthWrite:false,transparent:true,blending:THREE.AdditiveBlending}));this.scene.add(this.stars);
  const m=new THREE.ShaderMaterial({depthWrite:false,depthTest:false,uniforms:{amount:{value:this.o.nebula_intensity}},
   vertexShader:'varying vec2 v;void main(){v=uv;gl_Position=vec4(position.xy,1.,1.);}',
   fragmentShader:'varying vec2 v;uniform float amount;void main(){vec2 p=(v-.5)*2.;float n=pow(max(0.,sin(p.x*2.+.3)*cos(p.y*3.)),3.)*amount;gl_FragColor=vec4(vec3(.12,.21,.38)*n,1.);}'});
  this.nebula=new THREE.Mesh(new THREE.PlaneGeometry(2,2),m);this.nebula.frustumCulled=false;this.nebula.renderOrder=-10;this.scene.add(this.nebula);
 }
 makeCore(){
  const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array([0,0,0]),3));
  this.core=new THREE.Points(g,new THREE.ShaderMaterial({vertexShader:coreVertex,fragmentShader:coreFragment,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,
   uniforms:{size:{value:this.o.core_size_px},pixelRatio:{value:this.pixelRatio},fade:{value:1}}}));
  this.core.frustumCulled=false;this.core.renderOrder=5;this.scene.add(this.core);
 }
 add(field,kind,intensity){
  const o=this.o,g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(field.position,3));g.setAttribute('aSeed',new THREE.BufferAttribute(field.seed,4));g.setAttribute('aFlow',new THREE.BufferAttribute(field.flow,1));
  if(field.letter)g.setAttribute('aLetter',new THREE.BufferAttribute(field.letter,3));
  if(field.color)g.setAttribute('aGalaxy',new THREE.BufferAttribute(field.color,4));
  const uniforms={time:{value:0},intro:{value:1},kind:{value:kind},radius:{value:o.radius},innerRatio:{value:o.ring_inner_ratio},
   orbitSpeed:{value:o.orbit_speed},planetSpin:{value:o.planet_spin},swirlAmplitude:{value:o.swirl_amplitude},swirlSpeed:{value:o.swirl_speed},
   wanderFraction:{value:o.wander_fraction},wanderDistance:{value:o.wander_distance},pointSize:{value:o.point_size},pointMin:{value:o.point_size_min},pointMax:{value:o.point_size_max},
   pixelRatio:{value:this.pixelRatio},referenceDistance:{value:o.camera_distance},absorption:{value:o.absorption},intensity:{value:intensity},galaxySpin:{value:o.galaxy_spin},coreIntensity:{value:o.galaxy_core_intensity},textWidth:{value:o.text_width}};
  for(const [key,value] of Object.entries({outerRatio:o.ring_outer_ratio,ringLightPower:o.ring_light_power,ringOuterLight:o.ring_outer_light,
   brightFraction:o.bright_fraction,brightSize:o.bright_size,brightIntensity:o.bright_intensity,brightHalo:o.bright_halo,
   twinkleSpeed:o.twinkle_speed,rimStrength:o.rim_strength,rimPower:o.rim_power}))uniforms[key]={value};
  Object.assign(uniforms,{well:{value:new THREE.Vector3()},wellRadius:{value:1},eyeAlpha:{value:0},wellAttraction:{value:this.options.interaction?.well_attraction??0},wellBrightness:{value:this.options.interaction?.well_brightness??0},sceneFade:{value:1},scatter:{value:0},handScale:{value:1},scatterDistance:{value:this.options.interaction?.scatter_distance??0},scatterHeight:{value:this.options.interaction?.scatter_height??0},scatterScale:{value:o.radius}});
  const i=this.options.interaction;
  Object.assign(uniforms,{galaxyDrift:{value:o.galaxy_drift_fraction},galaxyDriftSpeed:{value:o.galaxy_drift_speed},galaxyPitchCot:{value:1/Math.tan(THREE.MathUtils.degToRad(o.galaxy_pitch_deg))}});
  Object.assign(uniforms,{spotSize:{value:i.spotlight_size_gain},spotTwinkle:{value:i.spotlight_twinkle},spotSpeed:{value:i.spotlight_speed},letterWeights:{value:this.letterFocus?.weights??this.emptyLetters},letterScale:{value:i.text_letter_scale-1},letterLift:{value:i.text_letter_lift},letterBrightness:{value:i.text_letter_brightness},galaxySwirl:{value:i.galaxy_scatter_swirl}});
  if(kind===3)uniforms.pointMax.value=o.text_point_size_max;
  Object.assign(uniforms,{sceneRotation:{value:0},textCurl:{value:i.text_scatter_curl},textStagger:{value:i.text_scatter_stagger}});
  if(kind===2){uniforms.wellAttraction.value=i.galaxy_well_attraction;uniforms.wellBrightness.value=i.galaxy_well_brightness;uniforms.scatterDistance.value=i.galaxy_scatter_distance;uniforms.scatterHeight.value=i.galaxy_scatter_height;uniforms.scatterScale.value=o.galaxy_radius;}
  if(kind===3){uniforms.wellAttraction.value=i.text_well_attraction;uniforms.scatterDistance.value=i.text_scatter_distance;uniforms.scatterHeight.value=i.text_scatter_height;uniforms.scatterScale.value=o.text_width/2;uniforms.wellBrightness.value=i.text_well_brightness;}
  for(const [prefix,colors] of [['warm',o.planet_colors],['cool',o.ring_colors]])for(let i=0;i<3;i++)uniforms[prefix+'ABC'[i]]={value:new THREE.Color(colors[i])};
  const points=new THREE.Points(g,new THREE.ShaderMaterial({vertexShader:particleVertex,fragmentShader:particleFragment,uniforms,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending}));
  points.material.defaultAttributeValues.aLetter=[0,0,0];
  points.material.defaultAttributeValues.aGalaxy=[0,0,0,0];
  points.frustumCulled=false;this.scene.add(points);this.items.push(points);
 }
 setScene(name){
  if(!['planet','galaxy','text'].includes(name))throw new Error('未知场景');
  const o=this.o,count=this.quality?.particles??(o.planet_particles+o.ring_particles);
  if(name===this.sceneName&&this.count===count)return;this.count=count;
  for(const item of this.items){this.scene.remove(item);item.geometry.dispose();item.material.dispose();}this.items=[];this.sceneName=name;
  const budget=particleBudget(count,o);
  if(name==='planet'){this.add(planetField(budget.planet,o),0,o.planet_intensity*budget.intensity);this.add(ringField(budget.ring,o),1,o.ring_intensity*budget.intensity);}
  else if(name==='galaxy')this.add(galaxyField(count,o),2,o.galaxy_intensity*budget.intensity);
  else{const m=this.mask??=textMask(this.text),f=textField(count,o,m.mask,m.width,m.height,m.ranges);this.textBounds=f.bounds;this.letterFocus??=new LetterFocus(f.letters,this.options.interaction.text_letter_spring_hz);this.add(f,3,o.text_intensity*budget.intensity);}
  this.core.visible=name!=='text';
 }
 resize(){
  this.pixelRatio=Math.min(devicePixelRatio,this.quality?.ratio??this.options.pixel_ratio);
  if(this.sceneName)this.setScene(this.sceneName);
  for(const item of [...this.items,this.core])item.material.uniforms.pixelRatio.value=this.pixelRatio;
  this.renderer.setPixelRatio(this.pixelRatio);this.renderer.setSize(innerWidth,innerHeight,false);
  this.composer.setPixelRatio(this.pixelRatio);this.composer.setSize(innerWidth,innerHeight);
  this.camera.aspect=innerWidth/innerHeight;this.camera.updateProjectionMatrix();
 }
 placeCamera(view='oblique',control=null){
  const o=this.o,pose=scenePose(this.sceneName,o,this.options.interaction,this.camera.aspect,control,this.modelScale,this.textBounds,view);
  const {pitch,distance,yaw}=pose;this.pose=pose;
  const p=THREE.MathUtils.degToRad(pitch),y=THREE.MathUtils.degToRad(yaw);
  this.camera.position.set(Math.sin(y)*Math.cos(p)*distance,Math.sin(p)*distance,Math.cos(y)*Math.cos(p)*distance);
  this.camera.lookAt(0,0,0);this.camera.rotateZ(THREE.MathUtils.degToRad(pose.roll));this.camera.updateMatrixWorld();
 }
 pick(xy){
  // Viewport pixels -> graphics projection only; no screen/global coordinate mapping here.
  this.pointer.set(xy[0]/innerWidth*2-1,1-xy[1]/innerHeight*2);
  this.raycaster.setFromCamera(this.pointer,this.camera);
  this.raycaster.ray.origin.toArray(this.origin);this.raycaster.ray.direction.toArray(this.direction);
  return sceneHit(this.sceneName,this.origin,this.direction,this.o,this.modelScale,this.textBounds,this.options.interaction);
 }
 acceptScene(status,now=performance.now()){this.assembly.accept(status,now);if(status.scene_transition)this.burst.cancel();}
 acceptBurst(status,now=performance.now()){this.burst.accept(status,now);}
 resetControls(){this.assembly.reset(Math.max(0,['planet','galaxy','text'].indexOf(this.sceneName)));this.burst.reset();this.clockMs=0;this.transitionInfo=null;this.letterFocus?.reset();this.director?.reset();this.hands?.reset();this.modelScale=1;this.fade=1;this.pending=null;
  for(const item of this.items){const u=item.material.uniforms;u.eyeAlpha.value=0;u.scatter.value=0;u.handScale.value=1;}}
 reset(){this.resetControls();this.motionTime=0;this.setScene('planet');this.assembly.reset(0);this.placeCamera();}
 applyWell(frame,control,dt,intro=1){
  const well=control.eye?this.pick(frame.xy).well:null;
  // Project error radius at the interaction plane's view depth (not far-camera distance).
  const depth=well?Math.max(.1,-this.depthPoint.set(...well).applyMatrix4(this.camera.matrixWorldInverse).z):0;
  for(const item of this.items){const u=item.material.uniforms;
   u.eyeAlpha.value=intro<1?0:well?control.eyeAlpha:u.eyeAlpha.value*Math.exp(-dt/this.options.interaction.gaze_fade_s);
   if(well){u.well.value.set(...well).divideScalar(this.modelScale);
    u.wellRadius.value=Math.max(...frame.radius)*2*Math.tan(THREE.MathUtils.degToRad(this.o.field_of_view/2))*depth/innerHeight*this.options.interaction.well_radius_scale/this.modelScale;}}
 }
 updateControls(frame,dt,intro,pattern=frame?.pattern,view='oblique',now=null){
  this.clockMs=now??this.clockMs+dt*1000;
  if(!this.director){this.director=new PlanetDirector(this.options);this.hands=new HandDynamics(this.options);}
  if(!this.assembly.external)this.assembly.request(intro<1?0:pattern??Math.max(0,['planet','galaxy','text'].indexOf(this.sceneName)),this.clockMs);
  const transition=this.assembly.sample(this.clockMs);this.transitionInfo=transition;
  this.setScene(['planet','galaxy','text'][intro<1?0:transition.pattern]);
  const enabled=intro>=1&&!transition.busy;
  this.burst.accept(frame,this.clockMs);if(!enabled)this.burst.cancel();
  const hand=this.hands.update(frame,dt,enabled?1:0);this.modelScale=enabled?hand.scale:1;
  const hit=enabled&&frame?.gaze_valid?this.pick(frame.xy):null;
  const control=this.director.update(frame,dt,[innerWidth,innerHeight],!!hit?.hit,enabled?1:0,this.sceneName);
  const scatter=enabled?Math.max(hand.scatter,this.burst.value(this.clockMs)):0;this.appliedScatter=scatter;
  this.motionTime+=dt*(1+(enabled?hand.spin:0));this.placeCamera(view,control);
  if(this.letterFocus){
   const well=enabled&&control.eye&&this.sceneName==='text'?this.pick(frame.xy).well:null,point=well?.map(v=>v/this.modelScale);
   this.letterFocus.step(letterHit(this.letterFocus.letters,point,this.letterFocus.weights,this.options.interaction.text_letter_scale-1),dt);
  }
  for(const item of this.items){const u=item.material.uniforms;u.scatter.value=scatter;u.handScale.value=this.modelScale;if(this.letterFocus)u.letterWeights.value=this.letterFocus.weights;}
  this.applyWell(frame,control,dt,enabled?1:0);return control;
 }
 draw(frame,dt,time,intro,pattern=frame?.pattern){
  const control=this.updateControls(frame,dt,intro,pattern,'oblique',performance.now());
  this.render(this.motionTime,intro,'oblique',control);
 }
 render(time,intro=1,view='oblique',control=null){
  this.placeCamera(view,control);
  if(this.transitionInfo?.busy)intro=Math.min(intro,this.transitionInfo.assembly);
  this.bloom.strength=this.o.bloom_strength+(this.sceneName==='galaxy'?(control?.zoom??0)*this.o.galaxy_zoom_bloom_gain:0);
  for(const item of this.items){item.material.uniforms.sceneRotation.value=THREE.MathUtils.degToRad(this.pose.rotation);item.material.uniforms.time.value=time;item.material.uniforms.intro.value=intro;item.material.uniforms.sceneFade.value=1;}
  this.core.material.uniforms.fade.value=intro;this.composer.render();
 }
 close(){this.dispose();}

 dispose(){for(const item of [...this.items,this.stars,this.core,this.nebula]){item.geometry.dispose();item.material.dispose();}for(const pass of this.composer.passes)pass.dispose?.();this.composer.dispose();this.renderer.dispose();}
}
