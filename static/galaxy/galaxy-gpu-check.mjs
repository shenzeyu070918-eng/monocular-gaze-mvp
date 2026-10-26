// Imported ONLY by ?preview diagnostics. No readback/program compilation in live draw.
import {particleVertex} from './planet-shaders.mjs';
import {compareArms} from './galaxy-stability.mjs';
export function checkGalaxyGPU(renderer,times){
 if(renderer.sceneName!=='galaxy')throw new Error('请先选择银河');
 const gl=renderer.renderer.getContext(),item=renderer.items[0],attributes=item.geometry.attributes,o=renderer.o;
 const indices=[];for(let n=0;n<attributes.aFlow.count;n++)if(attributes.aFlow.array[n]===1)indices.push(n);
 const step=Math.max(1,Math.ceil(indices.length/16000)),chosen=indices.filter((_,n)=>n%step===0),N=chosen.length;
 const shaders=[],buffers=[];let program,vao,feedback;
 function compile(type,source){const s=gl.createShader(type);shaders.push(s);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;}
 try{
  const marker='vec4 world=modelMatrix*vec4(p,1.);';if(!particleVertex.includes(marker))throw new Error('无法定位正式顶点变换');
  const source='#version 300 es\nprecision highp float;\nin vec3 position;uniform mat4 modelMatrix,viewMatrix,projectionMatrix;uniform vec3 cameraPosition;out vec3 diagnosticPosition;\n'+particleVertex.replaceAll('attribute ','in ').replaceAll('varying ','out ').replace(marker,'diagnosticPosition=p;'+marker);
  program=gl.createProgram();gl.attachShader(program,compile(gl.VERTEX_SHADER,source));gl.attachShader(program,compile(gl.FRAGMENT_SHADER,'#version 300 es\nprecision highp float;out vec4 color;void main(){color=vec4(0.);}'));
  gl.transformFeedbackVaryings(program,['diagnosticPosition'],gl.SEPARATE_ATTRIBS);gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));gl.useProgram(program);
  vao=gl.createVertexArray();gl.bindVertexArray(vao);
  for(const name of ['position','aSeed','aFlow','aGalaxy']){
   const location=gl.getAttribLocation(program,name);if(location<0)continue;
   const attr=attributes[name],size=attr.itemSize,data=new Float32Array(N*size);
   chosen.forEach((index,n)=>data.set(attr.array.subarray(index*size,index*size+size),n*size));
   const buffer=gl.createBuffer();buffers.push(buffer);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,size,gl.FLOAT,false,0,0);
  }
  for(const [name,u] of Object.entries(item.material.uniforms)){
   const location=gl.getUniformLocation(program,name);if(location===null)continue;
   const value=u.value;if(typeof value==='number')gl.uniform1f(location,value);else if(value.toArray)gl.uniform3fv(location,value.toArray());
  }
  const identity=new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]);
  for(const name of ['modelMatrix','viewMatrix','projectionMatrix']){const l=gl.getUniformLocation(program,name);if(l!==null)gl.uniformMatrix4fv(l,false,identity);}
  for(const [name,value] of Object.entries({kind:2,scatter:0,eyeAlpha:0,intro:1,handScale:1,sceneRotation:0})){const l=gl.getUniformLocation(program,name);if(l!==null)gl.uniform1f(l,value);}
  const output=gl.createBuffer();buffers.push(output);gl.bindBuffer(gl.TRANSFORM_FEEDBACK_BUFFER,output);gl.bufferData(gl.TRANSFORM_FEEDBACK_BUFFER,N*12,gl.STREAM_READ);
  feedback=gl.createTransformFeedback();gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK,feedback);gl.bindBufferBase(gl.TRANSFORM_FEEDBACK_BUFFER,0,output);
  const samples=[];
  gl.enable(gl.RASTERIZER_DISCARD);
  for(const time of times){
   gl.uniform1f(gl.getUniformLocation(program,'time'),time);gl.beginTransformFeedback(gl.POINTS);gl.drawArrays(gl.POINTS,0,N);gl.endTransformFeedback();
   const points=new Float32Array(N*3);gl.getBufferSubData(gl.TRANSFORM_FEEDBACK_BUFFER,0,points);samples.push(points);
  }
  gl.disable(gl.RASTERIZER_DISCARD);
  const checks=samples.map((positions,n)=>compareArms(samples[0],positions,times[0],times[n],o));
  return {source:'actual_particle_vertex_shader_transform_feedback',times_s:times,sample_count:N,checks,passed:checks.every(c=>c.passed)};
 }finally{
  gl.disable(gl.RASTERIZER_DISCARD);gl.bindTransformFeedback(gl.TRANSFORM_FEEDBACK,null);gl.bindVertexArray(null);gl.useProgram(null);
  for(const b of buffers)gl.deleteBuffer(b);for(const s of shaders)gl.deleteShader(s);if(program)gl.deleteProgram(program);if(vao)gl.deleteVertexArray(vao);if(feedback)gl.deleteTransformFeedback(feedback);
  renderer.renderer.resetState();
 }
}
