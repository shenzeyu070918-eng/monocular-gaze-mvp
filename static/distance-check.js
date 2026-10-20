import {percentile} from './math.js';

// Only raw camera-depth estimates enter this check; gaze errors and personal
// correction matrices are deliberately absent from its interface.
export class DistanceCheck {
 constructor(config){
  for(const key of ['settle_ms','duration_ms','warning_cm'])if(!Number.isFinite(config[key])||config[key]<=0)throw Error('视距自检配置无效');
  if(!Number.isInteger(config.min_samples)||config.min_samples<1||!Array.isArray(config.distances_cm)||!config.distances_cm.length||config.distances_cm.some(v=>!Number.isFinite(v)||v<=0))throw Error('视距自检配置无效');
  this.config=config;this.active=false;
 }
 start(measured,now){
  if(this.active||!Number.isFinite(measured)||measured<=0||!Number.isFinite(now)||now<0)throw Error('视距自检状态无效');
  this.measured=measured;this.begin=now;this.samples=[];this.active=true;
 }
 add(estimate,capturedAt){
  if(!this.active||!Number.isFinite(estimate)||estimate<=0||!Number.isFinite(capturedAt))return;
  const start=this.begin+this.config.settle_ms;
  if(capturedAt<start||capturedAt>start+this.config.duration_ms)return;
  this.samples.push({value:estimate,time:capturedAt});
 }
 ready(now){return this.active&&now>=this.begin+this.config.settle_ms+this.config.duration_ms;}
 finish(now){
  if(!this.ready(now))throw Error('视距自检采样尚未完成');
  this.active=false;
  const count=this.samples.length,estimate=count>=this.config.min_samples?percentile(this.samples.map(s=>s.value),.5):null;
  const bias=estimate===null?null:estimate-this.measured;
  return {measured_cm:this.measured,estimated_cm:estimate,bias_cm:bias,bias_percent:bias===null?null:bias/this.measured*100,
   sample_count:count,sample_start_ms:count?Math.min(...this.samples.map(s=>s.time)):null,
   sample_end_ms:count?Math.max(...this.samples.map(s=>s.time)):null,
   browser_time_ms:now,warning:bias===null||Math.abs(bias)>this.config.warning_cm};
 }
}

export function cameraColumns(camera){
 return {intrinsics_source:camera?.intrinsics_source??'unknown',device_file:camera?.device_file??null,
  intrinsics_reason:camera?.intrinsics_reason??null,undistort_enabled:camera?.undistort_enabled??null,
  undistort_applied:camera?.undistort_applied??null,intrinsics_scaled:camera?.scaled??null,
  camera_width_px:camera?.width_px??null,camera_height_px:camera?.height_px??null};
}

export function cameraStatus(camera){
 if(!camera)return '等待相机内参状态';
 const reasons={no_device_file:'未配置设备文件',aspect_ratio_mismatch:'实际分辨率与标定长宽比不同，已回退估计值'};
 return `${camera.intrinsics_source==='calibrated'?'标定内参':'估计内参'} · ${camera.width_px}×${camera.height_px}${camera.scaled?' · 已等比例缩放':''}${camera.intrinsics_reason?' · '+reasons[camera.intrinsics_reason]:''} · 去畸变${camera.undistort_applied?'已应用':camera.undistort_enabled?'已开启，当前未应用':'已关闭'}${camera.device_file?' · '+camera.device_file:''}`;
}
