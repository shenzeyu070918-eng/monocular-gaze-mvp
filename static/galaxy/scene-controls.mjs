// Python owns scene selection. A revision prevents old in-flight frames undoing a key step.
export class SceneSelection{
 constructor(){this.reset();}
 reset(){this.pattern=0;this.revision=-1;this.status=null;}
 accept(status){
  if(!status||!Number.isInteger(status.pattern)||status.pattern<0||status.pattern>2||!Number.isInteger(status.scene_revision)||status.scene_revision<0||status.scene_revision<this.revision)return false;
  if(status.scene_revision===this.revision&&status.pattern!==this.pattern)return false;
  if(status.scene_revision===this.revision&&this.status?.scene_transition===0&&status.scene_transition===1)return false;
  this.status=status;this.pattern=status.pattern;this.revision=status.scene_revision;return true;
 }
}
export function sceneDirection(event){
 if(event.repeat||event.ctrlKey||event.altKey||event.metaKey)return 0;
 if(event.key==='ArrowRight'||event.key===' '||event.code==='Space')return 1;
 return event.key==='ArrowLeft'?-1:0;
}
export function cornerVisual(status,usable=true){
 if(!usable||!status||status.corner<0||status.corner>3)return {corner:-1,progress:0,opacity:0};
 return {corner:status.corner,progress:Math.max(0,Math.min(1,status.corner_progress)),
  opacity:status.corner_state===3?0:status.corner_state===2?.4:.75};
}
const corners=['左上','右上','右下','左下'],reasons=['尚未清零','离开','断档','冷却','无效'],states=['未停留','区域内','宽限中','冷却中'];
export function cornerDiagnostic(frame,status,size){
 const uv=frame?.gaze_valid?frame.xy?.map((v,i)=>v/size[i]):null;
 const outside=uv?.map(v=>v<0?v:v>1?v-1:0);
 return '视口 uv '+(uv?uv.map(v=>v.toFixed(3)).join(' / '):'—')+' · 越界 '+(outside?outside.map(v=>v.toFixed(3)).join(' / '):'—')+(frame?.gaze_valid?'':' · 视线无效')+
  '\n'+(status?.switch_mode===1?'边缘 ':'角落 ')+((status?.switch_mode===1?['左（上一个）','右（下一个）']:corners)[status?.corner]??'无')+' · '+(states[status?.corner_state]??'未停留')+' · 停留 '+(status?.corner_dwell_ms??0).toFixed(0)+' ms · 进度 '+((status?.corner_progress??0)*100).toFixed(0)+'%'+
  ' · 最近清零 '+(reasons[status?.corner_reset]??reasons[0]);
}

const entryReasons=['已采用','时钟尚未对齐','屏幕尺寸未实测','样本不足','样本筛选未通过','偏移超过上限','缺少修正后坐标'];
export function entryDiagnostic(s){return '进入偏移 '+(s?(s.applied?'已采用':'保留原偏移')+' · '+(entryReasons[s.reason]??'无效')+' · '+s.offset_cm.map(v=>v.toFixed(2)).join(' / ')+' cm（候选） · 当前 '+s.used_offset_cm.map(v=>v.toFixed(2)).join(' / ')+' cm · 样本 '+s.samples:'尚未刷新');}
