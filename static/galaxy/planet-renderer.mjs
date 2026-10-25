import * as THREE from 'three';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {planetField,ringField,galaxyField,textField,randomSource} from './particle-fields.mjs';
import {particleVertex,particleFragment,coreVertex,coreFragment} from './planet-shaders.mjs';

function textMask(text){
 const canvas=document.createElement('canvas');canvas.width=1024;canvas.height=384;
 const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.font='900 240px Arial, sans-serif';
 ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='white';ctx.fillText(text,512,192,960);
 const data=ctx.getImageData(0,0,1024,384).data,mask=new Uint8Array(1024*384);
 for(let i=0;i<mask.length;i++)mask[i]=Number(data[i*4+3]>128);
 return {mask,width:1024,height:384};
}

export class PlanetRenderer{
 constructor(canvas,options){
  this.o=options.visual;this.text=options.text;this.pixelRatio=Math.min(devicePixelRatio,options.pixel_ratio);this.items=[];this.sceneName='';
  const context=canvas.getContext('webgl2',{alpha:false,antialias:false,powerPreference:'high-performance'});
  if(!context)throw new Error('需要WebGL2，请使用支持硬件加速的Chrome');
  if(!context.getExtension('EXT_color_buffer_float'))throw new Error('需要浮点颜色缓冲，请启用浏览器硬件加速');
  this.renderer=new THREE.WebGLRenderer({canvas,context,antialias:false});this.renderer.setClearColor(0x000000);
  this.renderer.toneMapping=THREE.ACESFilmicToneMapping;this.renderer.toneMappingExposure=this.o.exposure;
  this.scene=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(this.o.field_of_view,1,.05,150);
  this.composer=new EffectComposer(this.renderer);this.composer.addPass(new RenderPass(this.scene,this.camera));
  this.bloom=new UnrealBloomPass(new THREE.Vector2(1,1),this.o.bloom_strength,this.o.bloom_radius,this.o.bloom_threshold);
  this.composer.addPass(this.bloom);this.composer.addPass(new OutputPass());
  this.background();this.makeCore();this.setScene('planet');this.resize();
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
  const uniforms={time:{value:0},intro:{value:1},kind:{value:kind},radius:{value:o.radius},innerRatio:{value:o.ring_inner_ratio},
   orbitSpeed:{value:o.orbit_speed},planetSpin:{value:o.planet_spin},swirlAmplitude:{value:o.swirl_amplitude},swirlSpeed:{value:o.swirl_speed},
   wanderFraction:{value:o.wander_fraction},wanderDistance:{value:o.wander_distance},pointSize:{value:o.point_size},pointMin:{value:o.point_size_min},pointMax:{value:o.point_size_max},
   pixelRatio:{value:this.pixelRatio},referenceDistance:{value:o.camera_distance},absorption:{value:o.absorption},intensity:{value:intensity},galaxySpin:{value:o.galaxy_spin},coreIntensity:{value:o.galaxy_core_intensity},textWidth:{value:o.text_width}};
  for(const [key,value] of Object.entries({outerRatio:o.ring_outer_ratio,ringLightPower:o.ring_light_power,ringOuterLight:o.ring_outer_light,
   brightFraction:o.bright_fraction,brightSize:o.bright_size,brightIntensity:o.bright_intensity,brightHalo:o.bright_halo,
   twinkleSpeed:o.twinkle_speed,rimStrength:o.rim_strength,rimPower:o.rim_power}))uniforms[key]={value};
  for(const [prefix,colors] of [['warm',o.planet_colors],['cool',o.ring_colors]])for(let i=0;i<3;i++)uniforms[prefix+'ABC'[i]]={value:new THREE.Color(colors[i])};
  const points=new THREE.Points(g,new THREE.ShaderMaterial({vertexShader:particleVertex,fragmentShader:particleFragment,uniforms,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending}));
  points.frustumCulled=false;this.scene.add(points);this.items.push(points);
 }
 setScene(name){
  if(!['planet','galaxy','text'].includes(name))throw new Error('未知预览场景');if(name===this.sceneName)return;
  for(const item of this.items){this.scene.remove(item);item.geometry.dispose();item.material.dispose();}this.items=[];this.sceneName=name;
  const o=this.o,count=o.planet_particles+o.ring_particles;
  if(name==='planet'){this.add(planetField(o.planet_particles,o),0,o.planet_intensity);this.add(ringField(o.ring_particles,o),1,o.ring_intensity);}
  else if(name==='galaxy')this.add(galaxyField(count,o),2,o.galaxy_intensity);
  else{const m=textMask(this.text);this.add(textField(count,o,m.mask,m.width,m.height),3,o.text_intensity);}
  this.core.visible=name!=='text';
 }
 resize(){
  this.renderer.setPixelRatio(this.pixelRatio);this.renderer.setSize(innerWidth,innerHeight,false);
  this.composer.setPixelRatio(this.pixelRatio);this.composer.setSize(innerWidth,innerHeight);
  this.camera.aspect=innerWidth/innerHeight;this.camera.updateProjectionMatrix();
 }
 render(time,intro=1,view='oblique'){
  const o=this.o,planet=this.sceneName==='planet',text=this.sceneName==='text';
  let pitch=text?0:o.camera_pitch_deg,distance=text?o.camera_distance:planet?o.camera_distance:o.galaxy_distance;
  if(view==='top')pitch=75;
  if(view==='close')distance=o.close_distance;
  const p=THREE.MathUtils.degToRad(pitch),y=THREE.MathUtils.degToRad(o.camera_yaw_deg);
  this.camera.position.set(Math.sin(y)*Math.cos(p)*distance,Math.sin(p)*distance,Math.cos(y)*Math.cos(p)*distance);
  this.camera.lookAt(0,0,0);this.camera.rotateZ(THREE.MathUtils.degToRad(text?0:o.camera_roll_deg));
  for(const item of this.items){item.material.uniforms.time.value=time;item.material.uniforms.intro.value=intro;}
  this.core.material.uniforms.fade.value=intro;this.composer.render();
 }
 dispose(){for(const item of [...this.items,this.stars,this.core,this.nebula]){item.geometry.dispose();item.material.dispose();}for(const pass of this.composer.passes)pass.dispose?.();this.composer.dispose();this.renderer.dispose();}
}
