import {evaluationTargets,evaluationFrames,summaryText} from './evaluation.js';
import {DistanceCheck,cameraColumns,cameraStatus} from './distance-check.js';
import {affine,percentile,summary,errorDistance} from './math.js';
import {RecordingSession,drawCapture,videoFrameTiming,screenPointToStage} from './recording.js';
import {generateJumpPoints,jumpLayout} from './jump.js';
const $=id=>document.getElementById(id), stage=$('stage'),video=$('video');
const capture=document.createElement('canvas'),ctx=capture.getContext('2d',{willReadFrequently:true});
let ready=false,running=false,starting=false,stream=null,generation=0,matrix=null,task=null,records=[],lastValid=0,smooth=null,trail=[],ticks=[],controller=null,calibrationEpoch=0;
let calibrating=false,reportCm=false,reportMatrix=false,runId='',calibrationId='none';
let recordingRequestTimeout=null,cameraStopping=false,jumpConfigReady=false,taskOpening=false,taskClosing=false,frameId=0,latestVideoFrame=null,videoCallbackId=null;
let evaluationConfig=null,inFlight=null,displayRevision=0;
let distanceConfig=null,distanceRun=null,lastCamera=null;
const pageClockId=crypto.randomUUID();
function resetDisplay(){displayRevision++;$('filterState').textContent='等待新的取帧样本';smooth=null;trail=[];$('gaze').hidden=true;$('gazeRaw').hidden=true;}
const recording=new RecordingSession({request:recordingRequest,onState:recordingState,onError:text=>{ $('recordingError').hidden=false;$('recordingError').textContent=`录制失败：${text}。已写入数据仍保留，请检查本地服务和录制文件。`; }});
async function recordingRequest(path,body){
 const configRequest=path==='/api/recording/config';
 if(!configRequest&&!Number.isFinite(recordingRequestTimeout))throw Error('录制配置尚未就绪，请稍后重试。');
 const abort=new AbortController(),timeout=configRequest?null:setTimeout(()=>abort.abort(),recordingRequestTimeout);
 try{const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},signal:abort.signal,...(body===undefined?{}:{body:JSON.stringify(body)})});let value;try{value=await response.json();}catch(error){if(error.name==='AbortError')throw error;throw Error('录制服务返回了无效响应');}if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'录制请求失败');return value;}
 catch(error){if(error.name==='AbortError')throw Error('录制请求超时，已停止等待；请检查本地服务和录制文件。');throw error;}
 finally{if(timeout!==null)clearTimeout(timeout);}
}
function recordingState(state){$('recording').checked=state.active;if(state.active&&!state.error)$('recordingError').hidden=true;$('recording').disabled=state.busy||!!task||!!distanceRun||taskOpening||taskClosing||calibrating;$('recordingState').textContent=state.active?`正在录制${state.manual?'':'（任务自动开启）'} · ${state.session?.file||state.session?.session_id}`:state.busy?'正在结束录制…':'未录制 · 不保存图像';buttons();}
function screenMetadata(){const r=stage.getBoundingClientRect();return {width_px:screen.width,height_px:screen.height,device_pixel_ratio:devicePixelRatio,window_width_px:innerWidth,window_height_px:innerHeight,fullscreen:!!document.fullscreenElement,stage_left_px:r.left,stage_top_px:r.top,stage_width_px:r.width,stage_height_px:r.height,screen_x:screenX,screen_y:screenY,outer_width_px:outerWidth,outer_height_px:outerHeight};}
function recordingMetadata(){return {page_clock_id:pageClockId,screen:screenMetadata(),geometry:geometry(),measured:$('measured').checked,grayscale:$('gray').checked,camera_width:capture.width||640,camera_height:capture.height||480,browser_camera:browserCamera()};}
function browserCamera(){const track=stream?.getVideoTracks?.()[0],info=track?.getSettings?.();return {device_id:info?.deviceId||null,label:track?.label||null};}
function calibrationEvent(){return {action:matrix?'applied':'reset',matrix:matrix?.map(row=>[...row])||null,calibration_id:calibrationId,group:matrix?'B':'A',coordinate_system:'full_screen_normalized'};}
function recordCalibration(){return recording.event({type:'calibration',...calibrationEvent(),browser_time_ms:performance.now()});}
function targetEvent(current,action,now,reason){if(current.displayedIndex===null)return;const p=current.points[current.displayedIndex];recording.event({type:'target',action,task_type:current.kind,task_id:current.id,index:current.displayedIndex,target:current.displayedTarget,stage_target:current.kind==='jump'?null:screenPointToStage(p,current.screen),browser_time_ms:now,...(current.targets?{target_id:current.targets[current.displayedIndex].target_id,region:current.targets[current.displayedIndex].region}:{}),...(reason?{reason}:{})});}
function watchVideoFrames(token){latestVideoFrame=null;if(typeof video.requestVideoFrameCallback!=='function')return;const callback=(now,metadata)=>{if(!running||token!==generation)return;latestVideoFrame={...metadata,callback_ms:now};videoCallbackId=video.requestVideoFrameCallback(callback);};videoCallbackId=video.requestVideoFrameCallback(callback);}

const configIds=['gray','widthCm','heightCm','topCm','horizontalCm','focalScale','measured'];
const geometry=()=>({width_cm:$('widthCm').value===''?null:+$('widthCm').value,height_cm:$('heightCm').value===''?null:+$('heightCm').value,top_cm:+$('topCm').value,horizontal_cm:+$('horizontalCm').value,focal_scale:+$('focalScale').value,measured:$('measured').checked});
const message=text=>$('message').textContent=text;
const wait=ms=>new Promise(r=>setTimeout(r,ms));
function buttons(){const busy=!!task||!!distanceRun||calibrating||taskOpening||taskClosing||recording.busy||cameraStopping;$('start').disabled=!ready||calibrating||starting||taskOpening||taskClosing||recording.busy||cameraStopping;$('calibrate').disabled=!running||busy||!jumpConfigReady;$('evaluate').disabled=!running||busy||!jumpConfigReady;$('jump').disabled=!running||busy||!jumpConfigReady;$('mode').disabled=busy;$('loadProfile').disabled=busy||!running||!!recording.session;$('compareDisplay').disabled=!running;configIds.forEach(id=>$(id).disabled=busy||starting||!!recording.session);['jumpCount','jumpDwell','jumpDistance','jumpMargin','profileName'].forEach(id=>$(id).disabled=busy);$('fullscreen').disabled=busy||starting;$('recording').disabled=busy||starting||!running||!ready||!jumpConfigReady;$('distanceStart').disabled=!running||!distanceConfig||!!task||taskOpening||taskClosing||recording.busy||cameraStopping||!!distanceRun?.check.active;$('distanceCancel').disabled=!distanceRun;$('start').textContent=running?'暂停并关闭摄像头':starting?'正在开启摄像头…':'开启摄像头';}
function resetCalibration(reason){resetDisplay();calibrationEpoch++;matrix=null;calibrationId='none';$('mode').value='raw';$('mode').options[1].disabled=true;smooth=null;trail=[];records=[];showResults();recordCalibration();if(reason)message(reason);}
function hideTask(){task=null;$('target').hidden=true;$('taskBanner').hidden=true;$('jumpOverlay').hidden=true;$('jumpEscape').hidden=true;document.documentElement.classList.remove('jump-active');buttons();}
async function summarizeEvaluation(current,completed,reason){
 try{
  const result=await recordingRequest('/api/evaluation/summary',{frames:evaluationFrames(current.records),targets:current.targets,geometry:current.geometry,matrix:current.matrix,session_id:recording.session?.session_id||null,task_id:current.id,run_id:current.runId,calibration_id:current.calibrationId,completed,reason,browser_time_ms:performance.now()});
  $('pointResults').textContent=summaryText(result,completed);
 }catch(error){$('pointResults').textContent=`逐点统计失败：${error.message}`;message(`评估汇总未保存：${error.message}`);if(recording.session)recording.fail(error);}
}
async function drainTask(current){const pending=inFlight;if(pending)await Promise.allSettled([pending]);current.acceptFrames=false;}
async function cancelTask(reason='cancelled'){
 const current=task;if(!current)return cancelDistance(reason);
 targetEvent(current,'end',performance.now(),reason);hideTask();taskClosing=true;buttons();
 try{await drainTask(current);if(current.kind==='evaluate'){showResults();await summarizeEvaluation(current,false,reason);}}
 finally{await recording.endTask(current.id,reason);taskClosing=false;buttons();}
}
function stop(){resetDisplay();cameraStopping=true;running=false;generation++;if(!recording.session)controller?.abort();if(videoCallbackId!==null)video.cancelVideoFrameCallback?.(videoCallbackId);videoCallbackId=null;stream?.getTracks().forEach(t=>t.stop());stream=null;video.srcObject=null;const ending=cancelTask('camera_paused');ending.then(async()=>{if(recording.session)await recording.stop('camera_paused');}).finally(()=>{cameraStopping=false;buttons();});$('landmarks').getContext('2d').clearRect(0,0,$('landmarks').width,$('landmarks').height);$('gaze').hidden=true;$('tracking').textContent='已暂停';$('fps').textContent='—';$('distance').textContent='—';$('previewEmpty').hidden=false;buttons();message('摄像头已关闭。重新开启后建议重新校准。');}
async function start(){
 if(cameraStopping||taskClosing||recording.busy)return;
 if(running){stop();return;}
 starting=true;buttons();message('请允许浏览器使用摄像头。');
 try{stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},facingMode:'user'},audio:false});video.srcObject=stream;await video.play();const scale=Math.min(640/video.videoWidth,480/video.videoHeight);capture.width=Math.round(video.videoWidth*scale);capture.height=Math.round(video.videoHeight*scale);running=true;generation++;ticks=[];lastValid=0;resetCalibration();$('welcome').hidden=true;$('previewEmpty').hidden=true;message(geometry().width_cm===null?'请先填写屏幕可视区域的实测宽高，或选择设备文件。视距自检仍可使用。':'摄像头已开启。建议先进入全屏，再做九点校准；期间保持坐姿稳定。');buttons();watchVideoFrames(generation);loop(generation);}
 catch(e){stream?.getTracks().forEach(t=>t.stop());stream=null;running=false;const reasons={NotAllowedError:'摄像头权限未开启。请在浏览器地址栏及系统「隐私与安全性 → 摄像头」中允许访问。',NotFoundError:'没有找到摄像头，请连接摄像头后重试。',NotReadableError:'摄像头暂时无法打开，请关闭占用摄像头的应用后重试。'};message(reasons[e.name]||`无法开启摄像头：${e.message}`);buttons();}finally{starting=false;buttons();}
}
function fullScreenPointToStage(raw){return screenPointToStage(raw,screenMetadata());}
function updatePreview(result){const c=$('landmarks');c.width=capture.width;c.height=capture.height;const p=c.getContext('2d');p.clearRect(0,0,c.width,c.height);p.fillStyle='#6ff0cc';for(const [x,y] of result.landmarks||[]){p.beginPath();p.arc(x*c.width,y*c.height,2,0,Math.PI*2);p.fill();}}
async function loop(token){
 while(running&&token===generation){const loopStart=performance.now(),begin=drawCapture(ctx,video,capture),currentFrame=++frameId,frameRecording=recording.frameContext(),frameTask=task&&task.displayedIndex!==null?{owner:task,index:task.displayedIndex,kind:task.kind,target:task.points[task.displayedIndex],elapsed:begin-task.since}:null;
  const viewRevision=displayRevision;
  const body={display_context:{generation:token,revision:viewRevision,matrix:$('mode').value==='calibrated'&&matrix?matrix.map(row=>[...row]):null},image:capture.toDataURL('image/jpeg',.78).split(',')[1],grayscale:$('gray').checked,geometry:geometry(),recording_session_id:frameRecording?.session_id||null,page_clock_id:pageClockId,frame_id:currentFrame,browser_capture_ms:begin,video_frame:videoFrameTiming(latestVideoFrame)};
  const operation=(async()=>{controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);let response,responseTime;try{body.browser_request_ms=performance.now();response=await fetch('/api/frame',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify(body)});responseTime=performance.now();}finally{clearTimeout(timeout);}if(!response.ok)throw Error('本地推理服务响应异常');const data=await response.json();if(frameRecording)recording.track(recording.receipt(frameRecording,currentFrame,responseTime));collectFrame(data,frameTask,begin);if(!running||token!==generation||viewRevision!==displayRevision)return;handleFrame(data,frameTask,begin);})();
  inFlight=operation;
  if(frameRecording)recording.track(operation);
  try{await operation;}
  catch(e){if(!running||token!==generation)return;message(e.name==='AbortError'?'单帧处理超时，请暂停后重试。':e.message);$('tracking').textContent='服务异常';$('gaze').hidden=true;$('gazeRaw').hidden=true;cancelTask('frame_error');await wait(600);}
  await wait(Math.max(0,33-(performance.now()-loopStart)));
 }
}
function placeDisplayPoint(id,p){
 const r=stage.getBoundingClientRect(),outside=p.some(v=>v<0||v>1),el=$(id);
 el.style.left=`${Math.max(9,Math.min(r.width-9,p[0]*r.width))}px`;el.style.top=`${Math.max(9,Math.min(r.height-9,p[1]*r.height))}px`;
 el.classList.toggle('outside',outside);el.hidden=!!task;return outside;
}
function renderDisplay(display,now){
 if(!display?.position){$('gaze').hidden=true;$('gazeRaw').hidden=true;smooth=null;$('filterState').textContent='显示滤波暂不可用';return;}
 smooth=fullScreenPointToStage(display.position);const outside=placeDisplayPoint('gaze',smooth);
 if($('compareDisplay').checked&&display.unfiltered)placeDisplayPoint('gazeRaw',fullScreenPointToStage(display.unfiltered));else $('gazeRaw').hidden=true;
 $('filterState').textContent=`${{fixation:'注视',saccade:'眼跳',candidate:'注视候选',unknown:'等待速度窗口'}[display.state]||display.state}${display.fallback_reason?' · 坐标速度回退（'+({missing_origin:'缺少面部中心',invalid_origin:'面部中心无效',unmeasured_geometry:'尺寸未实测'}[display.fallback_reason]||display.fallback_reason)+'）':''}`;
 if(!outside)trail.push({p:smooth,t:now});
 $('stageHint').textContent=outside?'预测在测试区之外。边界点仅提示方向；统计使用未裁剪坐标。':'绿色为 One Euro 显示点；校准和评估只使用未滤波、未裁剪的结果。';
}
function handleFrame(data,frameTask,capturedAt){
 const now=performance.now();if(data.camera){lastCamera=data.camera;$('cameraStatus').textContent=cameraStatus(data.camera);}distanceRun?.check.add(data.distance_cm,capturedAt);updatePreview(data);$('latency').textContent=`${Math.round(data.inference_ms)} ms`;
 if(data.valid){lastValid=now;ticks.push(now);$('tracking').textContent='跟踪中';$('distance').textContent=`${Math.round(data.distance_cm)} cm`;renderDisplay(data.display,now);}
 else{$('gazeRaw').hidden=true;$('filterState').textContent='无有效视线，已重置滤波';const labels={blink:'眨眼',no_face:'等待人脸',geometry_invalid:'视线不可用',prediction_error:'估计失败',screen_unconfigured:'请填写屏幕尺寸'};$('tracking').textContent=labels[data.status]||'暂时丢失';$('distance').textContent=Number.isFinite(data.distance_cm)?`${Math.round(data.distance_cm)} cm`:'—';$('stageHint').textContent=data.status==='screen_unconfigured'?'请填写屏幕可视区域的宽高；勾选实测前不输出厘米误差。':data.status==='no_face'?'请面向摄像头，在约 40–60 cm 距离坐稳，并保持眼部清晰可见。':'当前视线暂不可用，恢复跟踪后继续。';$('gaze').hidden=true;smooth=null;}
 ticks=ticks.filter(t=>now-t<2000);$('fps').textContent=`${(ticks.length/2).toFixed(1)} Hz`;
}
function collectFrame(data,captured,capturedAt){
 if(!captured||!captured.owner.acceptFrames||captured.elapsed<evaluationConfig.settle_ms||(captured.kind==='evaluate'&&captured.elapsed>evaluationConfig.dwell_ms))return;
 if(captured.kind==='calibrate'&&data.valid)captured.owner.sampleSets[captured.index].push(data.raw);
 if(captured.kind==='evaluate')record(data,captured,capturedAt);
}
function record(data,captured,capturedAt){
 const current=captured.owner,r=current.screen,g=current.geometry,targetFull=captured.target,target=screenPointToStage(targetFull,r);
 const a=data.valid?screenPointToStage(data.raw,r):null,corrected=data.valid&&current.matrix?affine(data.raw,current.matrix):null,b=corrected?screenPointToStage(corrected,r):null;
 const wcm=r.stage_width_px/r.width_px*g.width_cm,hcm=r.stage_height_px/r.height_px*g.height_cm;
 current.records.push({run_id:current.runId,calibration_id:current.calibrationId,capture_time_ms:Math.round(performance.timeOrigin+capturedAt),valid:data.valid,status:data.status,grayscale:$('gray').checked,geometry_estimated:data.camera?.intrinsics_source!=='calibrated',physical_dimensions_confirmed:g.measured,fullscreen:r.fullscreen,stage_width_px:r.stage_width_px,stage_height_px:r.stage_height_px,screen_width_cm:g.width_cm,screen_height_cm:g.height_cm,target_x:target[0],target_y:target[1],raw_screen_x:data.raw?.[0]??null,raw_screen_y:data.raw?.[1]??null,uncalibrated_x:a?.[0]??null,uncalibrated_y:a?.[1]??null,calibrated_x:b?.[0]??null,calibrated_y:b?.[1]??null,error_a_px:a?errorDistance(a,target,r.stage_width_px,r.stage_height_px):null,error_b_px:b?errorDistance(b,target,r.stage_width_px,r.stage_height_px):null,error_a_cm:a&&reportCm?errorDistance(a,target,wcm,hcm):null,error_b_cm:b&&reportCm?errorDistance(b,target,wcm,hcm):null,inference_ms:data.inference_ms,...cameraColumns(data.camera),region:current.targets[captured.index].region,target_id:current.targets[captured.index].target_id,target_screen_x:targetFull[0],target_screen_y:targetFull[1],calibrated_screen_x:corrected?.[0]??null,calibrated_screen_y:corrected?.[1]??null,origin_x_m:data.origin_m?.[0]??null,origin_y_m:data.origin_m?.[1]??null,origin_z_m:data.origin_m?.[2]??null,browser_capture_ms:capturedAt});
}
function jumpDisplayLayout(margin){const target=getComputedStyle($('jumpTarget')),hint=getComputedStyle($('jumpEscape'));return jumpLayout({margin,viewport:[innerWidth,innerHeight],target:[parseFloat(target.width),parseFloat(target.height)],hint:[parseFloat(hint.width),parseFloat(hint.height)]});}
async function beginTask(kind){
 if(!running||calibrating||task||distanceRun||taskOpening||taskClosing||recording.busy||cameraStopping)return;
 if(!jumpConfigReady){message('录制配置尚未就绪，请稍后重试。');return;}
 if(!document.fullscreenElement){message('校准、评估与跳点任务必须在全屏下进行。请先点击「全屏体验」，再开始。');return;}
 if(geometry().width_cm===null||geometry().height_cm===null){message('请先填写屏幕宽高，或在配置中选择设备文件。');return;}
 if(performance.now()-lastValid>1500){message('请先让摄像头检测到正面人脸，再开始。');return;}
 if(kind==='calibrate'&&!$('profileName').value.trim()){message('请填写个人档案的设备名。');return;}
 let points,dwellMs,margin,displayLayout,targets;
 if(kind==='jump'){try{dwellMs=+$('jumpDwell').value;margin=+$('jumpMargin').value/100;displayLayout=jumpDisplayLayout(margin);points=generateJumpPoints({point_count:+$('jumpCount').value,dwell_ms:dwellMs,min_distance:+$('jumpDistance').value,margin});}catch(error){message(error.message);return;}}
 else {try{margin=+$('jumpMargin').value/100;displayLayout=jumpDisplayLayout(margin);targets=kind==='evaluate'?evaluationTargets(evaluationConfig,margin):null;points=targets?targets.map(t=>t.target):evaluationConfig.calibration_points.map(p=>[...p]);}catch(error){message(error.message);return;}}
 const id=crypto.randomUUID(),token=generation;taskOpening=true;buttons();
 try{await recording.beginTask(id,recordingMetadata(),calibrationEvent());if(!running||token!==generation||!document.fullscreenElement){await recording.endTask(id,'start_cancelled');return;}
  if(kind==='calibrate')resetCalibration();else if(kind==='evaluate'){records=[];runId=crypto.randomUUID();reportCm=$('measured').checked&&!!document.fullscreenElement;reportMatrix=!!matrix;$('pointResults').textContent='';showResults();}
  displayLayout=jumpDisplayLayout(margin);document.documentElement.classList.add('jump-active');
  task={id,kind,points,targets,index:0,displayedIndex:null,since:0,sampleSets:points.map(()=>[]),dwellMs,displayLayout,screen:screenMetadata(),geometry:geometry(),matrix:matrix?.map(row=>[...row])||null,records,runId,calibrationId,acceptFrames:true};$('gaze').hidden=true;$('gazeRaw').hidden=true;trail=[];
  message(kind==='calibrate'?'只需依次注视蓝色圆点，不用点击。保持头部和屏幕位置稳定。':kind==='evaluate'?'评估不会更新校准参数。请依次注视蓝色圆点。':'请依次注视全屏目标点，按 Esc 取消。跳点数据不用于校准或评估。');
 }catch(error){await recording.endTask(id,'start_failed');message(`无法开始任务：${error.message}`);}finally{taskOpening=false;buttons();}
}
async function completeTask(done){
 targetEvent(done,'end',performance.now(),'completed');hideTask();taskClosing=true;buttons();
 try{await drainTask(done);
  if(done.kind==='calibrate')await finishCalibration(done.points.map((target,index)=>({target,raw:[0,1].map(i=>percentile(done.sampleSets[index].map(s=>s[i]),.5))})),done.screen);
  else if(done.kind==='evaluate'){showResults();await summarizeEvaluation(done,true,'completed');message(`评估完成。${reportCm?'厘米换算使用你确认的实测尺寸。':'未实测屏幕尺寸，厘米和角度不可用。'}`);}
  else message('跳点任务完成，原始数据已交给录制会话。');
 }finally{await recording.endTask(done.id,'completed');taskClosing=false;buttons();}
}

async function startDistance(){
 if(!running||task||taskOpening||taskClosing||calibrating||recording.busy||cameraStopping||!distanceConfig)return;
 if(!distanceRun){
  taskOpening=true;buttons();const id=crypto.randomUUID(),token=generation;
  try{await recording.beginTask(id,recordingMetadata(),calibrationEvent());if(!running||token!==generation){await recording.endTask(id,'start_cancelled');return;}distanceRun={id,index:0,check:new DistanceCheck(distanceConfig)};$('distanceResults').textContent='';}
  catch(error){message(`无法开始视距自检：${error.message}`);return;}finally{taskOpening=false;buttons();}
 }
 if(distanceRun.check.active)return;
 const measured=distanceConfig.distances_cm[distanceRun.index];distanceRun.check.start(measured,performance.now());
 $('distanceHint').textContent=`实测 ${measured} cm：请保持正对摄像头、坐姿稳定，正在采样。`;
 $('distanceStart').textContent='正在采样…';buttons();
}
function finishDistanceStep(now){
 const current=distanceRun,result=current.check.finish(now);
 recording.event({type:'distance_check',...result,camera:lastCamera});
 const signed=v=>`${v>=0?'+':''}${v.toFixed(1)}`;
 const line=result.estimated_cm===null?`${result.measured_cm} cm：有效样本不足（${result.sample_count}），请重试。`:
  `${result.measured_cm} cm：估计深度 ${result.estimated_cm.toFixed(1)} cm，偏差 ${signed(result.bias_cm)} cm（${signed(result.bias_percent)}%），样本 ${result.sample_count}。${result.warning?'偏差超过配置阈值，请核对尺量、相机标定与拍摄模式。':''}`;
 $('distanceResults').textContent+=($('distanceResults').textContent?'\n':'')+line;
 if(result.estimated_cm!==null)current.index++;
 if(current.index>=distanceConfig.distances_cm.length){$('distanceHint').textContent=recording.session&&!recording.error?'视距自检完成，结果已交给录制会话；请检查录制状态。':'视距自检完成，但录制已失败；请检查错误提示。';cancelDistance('completed');}
 else{$('distanceHint').textContent=`请先量好 ${distanceConfig.distances_cm[current.index]} cm，再点击采样。`;$('distanceStart').textContent=`采样 ${distanceConfig.distances_cm[current.index]} cm`;buttons();}
}
function cancelDistance(reason='cancelled'){
 if(!distanceRun)return Promise.resolve();const current=distanceRun;distanceRun=null;taskClosing=true;buttons();
 if(reason!=='completed')$('distanceHint').textContent='视距自检已取消，未完成的档位没有结果。';
 return Promise.resolve(recording.endTask(current.id,reason)).finally(()=>{taskClosing=false;$('distanceStart').textContent=`开始 ${distanceConfig.distances_cm[0]} cm 自检`;buttons();});
}

async function loadProfile(){
 if(!running||task||taskOpening||taskClosing||calibrating||recording.session)return;
 if(!document.fullscreenElement){message('请先进入全屏再加载个人档案。');return;}
 calibrating=true;buttons();const token=generation,epoch=calibrationEpoch;
 try{const result=await recordingRequest('/api/profile/load',{name:$('profileName').value.trim(),geometry:geometry(),screen:screenMetadata()});
  if(!running||token!==generation||epoch!==calibrationEpoch||!document.fullscreenElement)return;
  matrix=result.matrix;calibrationId=crypto.randomUUID();resetDisplay();$('mode').options[1].disabled=false;$('mode').value='calibrated';await recordCalibration();
  $('profileStatus').textContent=`已加载 ${result.file}，来源：九点校准；请确认相机输入模式与保存时一致。`;message('已明确启用 B 组个人修正。');
 }catch(error){$('profileStatus').textContent=`个人档案加载失败：${error.message}`;message(error.message);}finally{calibrating=false;buttons();}
}
async function finishCalibration(samples,screenInfo){
 calibrating=true;const epoch=calibrationEpoch;buttons();message('正在计算并保存个人修正参数…');
 try{
  if(!document.fullscreenElement)throw Error('已退出全屏，校准取消。');
  const id=crypto.randomUUID(),response=await fetch('/api/calibrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({samples,fullscreen:true,screen:{...screenInfo,width_cm:geometry().width_cm,height_cm:geometry().height_cm,measured:geometry().measured,horizontal_cm:geometry().horizontal_cm,top_cm:geometry().top_cm},profile_name:$('profileName').value.trim(),calibration_id:id})}),result=await response.json();
  if(epoch!==calibrationEpoch||!document.fullscreenElement)return;
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'校准失败，请重试。');
  matrix=result.matrix;calibrationId=id;$('mode').options[1].disabled=false;$('mode').value='calibrated';resetDisplay();await recordCalibration();
  $('profileStatus').textContent=result.profile_error?`个人档案保存失败：${result.profile_error}`:`个人档案已保存：data/profiles/${result.profile_file}`;
  $('profileStatus').classList.toggle('error',!!result.profile_error);
  message(result.profile_error?'校准已应用于本页面，但个人档案未保存，请检查上方提示。':'校准完成，个人档案已保存。请运行独立评估。');
 }catch(e){message(e.message);}finally{calibrating=false;buttons();}
}
function showTarget(current,now){
 const p=current.points[current.index];$('jumpOverlay').hidden=false;$('jumpTarget').style.left=`${p[0]*100}%`;$('jumpTarget').style.top=`${p[1]*100}%`;$('jumpEscape').hidden=!current.displayLayout.hintVisible;$('target').hidden=true;
 current.since=now;const action=current.displayedIndex===null?'appear':'switch';current.displayedIndex=current.index;current.displayedTarget=[...p];targetEvent(current,action,now);
}
function animate(){const now=performance.now();if(distanceRun?.check.ready(now))finishDistanceStep(now);if(task){if(!document.fullscreenElement)cancelTask('fullscreen_exited');else{if(task.displayedIndex!==task.index)showTarget(task,now);const age=now-task.since;if(task.kind==='jump'){if(age>=task.dwellMs){task.index++;if(task.index>=task.points.length)completeTask(task);else showTarget(task,now);}}else{$('taskTitle').textContent=`${task.kind==='calibrate'?'九点校准':'独立评估'} · ${task.index+1} / ${task.points.length}`;$('taskHint').textContent=task.kind==='calibrate'&&age>evaluationConfig.dwell_ms?'请继续注视，正在补足有效样本':'看向蓝色圆点，保持坐姿';$('progress').value=Math.min(1,age/evaluationConfig.dwell_ms);if(age>evaluationConfig.dwell_ms&&(task.kind==='evaluate'||task.sampleSets[task.index].length>=evaluationConfig.calibration_min_samples)){task.index++;if(task.index>=task.points.length)completeTask(task);else{showTarget(task,now);}}else if(age>evaluationConfig.max_dwell_ms){cancelTask('insufficient_samples');message('有效人脸样本不足，校准已取消。请调整光线或坐姿后重试。');}}}}

 if(running&&now-lastValid>1200){$('gaze').hidden=true;$('gazeRaw').hidden=true;}
 $('modeLabel').textContent=matrix&&$('mode').value==='calibrated'?'已个人校准 · 九点仿射修正':`未个人校准 · ${lastCamera?.intrinsics_source==='calibrated'?'标定内参':'估计内参'}`;
 const c=$('trailCanvas'),r=stage.getBoundingClientRect();if(c.width!==Math.round(r.width)||c.height!==Math.round(r.height)){c.width=Math.round(r.width);c.height=Math.round(r.height);}const p=c.getContext('2d');p.clearRect(0,0,c.width,c.height);trail=trail.filter(v=>now-v.t<650);if($('trail').checked&&!task&&trail.length>1){p.strokeStyle='#198d7845';p.lineWidth=2;p.beginPath();trail.forEach((v,i)=>p[i?'lineTo':'moveTo'](v.p[0]*r.width,v.p[1]*r.height));p.stroke();}requestAnimationFrame(animate);}
function showResults(){const valid=records.filter(r=>r.valid),unit=reportCm?'cm':'px',a=summary(valid.map(r=>r[`error_a_${unit}`])),b=summary(valid.map(r=>r[`error_b_${unit}`]));$('resultTable').hidden=!a;$('resultEmpty').hidden=!!a;$('resultEmpty').textContent=records.length?'没有有效样本。请调整坐姿后重新评估。':'注视随机目标点，测量原始输出误差。';$('export').disabled=!records.length;const rows=[['平均误差','mean'],['中位误差','median'],['P90 误差','p90']];if(reportCm)rows.push(['≤1.5 cm','within15'],['≤2 cm','within2']);$('resultBody').innerHTML=rows.map(([label,key])=>{const f=s=>!s?'—':key.startsWith('within')?`${(s[key]*100).toFixed(0)}%`:`${s[key].toFixed(1)} ${unit}`;return `<tr><td>${label}</td><td>${f(a)}</td><td>${reportMatrix?f(b):'未校准'}</td></tr>`;}).join('');$('validRate').textContent=records.length?`有效跟踪 ${Math.round(valid.length/records.length*100)}%（${valid.length}/${records.length} 帧）。仅统计稳定注视窗口；原始结果未平滑、未裁剪。`:'';}
function exportCsv(){if(!records.length)return;const keys=Object.keys(records[0]),csv='\uFEFF'+keys.join(',')+'\n'+records.map(row=>keys.map(k=>row[k]??'').join(',')).join('\n');const a=document.createElement('a');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));a.href=url;a.download=`gaze-evaluation-${new Date().toISOString().replaceAll(':','-')}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('start').onclick=start;$('calibrate').onclick=()=>beginTask('calibrate');$('evaluate').onclick=()=>beginTask('evaluate');$('jump').onclick=()=>beginTask('jump');$('distanceStart').onclick=startDistance;$('distanceCancel').onclick=()=>cancelDistance('cancelled');$('recording').onchange=async()=>{try{if($('recording').checked){if(!running||!ready||cameraStopping)throw Error('请先开启摄像头，再开始录制。');await recording.startManual(recordingMetadata(),calibrationEvent());}else await recording.stop('manual');}catch(error){message(error.message);}recordingState(recording.state());};$('cancel').onclick=()=>{cancelTask('cancelled');message('已取消本次任务。');};$('export').onclick=exportCsv;$('mode').onchange=resetDisplay;$('compareDisplay').onchange=()=>{$('compareLegend').hidden=!$('compareDisplay').checked;if(!$('compareDisplay').checked)$('gazeRaw').hidden=true;};$('loadProfile').onclick=loadProfile;
$('fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{message('当前浏览器不支持全屏，请在 Chrome 中打开本地地址。');}};
configIds.forEach(id=>$(id).addEventListener('change',()=>{const el=$(id);if(el.type==='number'&&!el.checkValidity()){el.value=el.defaultValue;message('参数超出范围，已恢复默认值。');}cancelTask('configuration_changed');resetCalibration('输入条件已改变，请重新校准。');}));
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);if(task)cancelTask('window_resized');resizeTimer=setTimeout(()=>{cancelTask('window_resized');resetCalibration('窗口尺寸已改变，请重新校准。');$('fullscreen').textContent=document.fullscreenElement?'退出全屏':'全屏体验';},200);});
document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement){if(task)cancelTask('fullscreen_exited');if(calibrating)resetCalibration('已退出全屏，校准取消。');}});
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&task)cancelTask('cancelled');});
window.addEventListener('pagehide',()=>{stream?.getTracks().forEach(t=>t.stop());if(recording.session){const session_id=recording.session.session_id,browser_time_ms=performance.now(),send=(path,value)=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),keepalive:true}).catch(()=>{});if(task&&task.displayedIndex!==null){const p=task.points[task.displayedIndex];send('/api/recording/event',{session_id,type:'target',action:'end',task_type:task.kind,task_id:task.id,index:task.displayedIndex,target:task.displayedTarget,stage_target:task.kind==='jump'?null:screenPointToStage(p,task.screen),browser_time_ms,reason:'page_closed'});}send('/api/recording/stop',{session_id,reason:'page_closed',browser_time_ms});}});
async function loadRecordingConfig(){try{const config=await recordingRequest('/api/recording/config');if(!Number.isFinite(config.request_timeout_ms)||config.request_timeout_ms<=0)throw Error('录制请求超时配置无效');recordingRequestTimeout=config.request_timeout_ms;$('jumpCount').value=config.jump.point_count;$('jumpDwell').value=config.jump.dwell_ms;$('jumpDistance').value=config.jump.min_distance;$('jumpMargin').value=config.jump.margin*100;
 if(config.geometry){for(const [id,key]of Object.entries({widthCm:'width_cm',heightCm:'height_cm',topCm:'top_cm',horizontalCm:'horizontal_cm',focalScale:'focal_scale'}))$(id).value=config.geometry[key]??'';$('measured').checked=config.geometry.measured;}
 evaluationConfig=config.evaluation;
 if(!evaluationConfig)throw Error('评估配置尚未就绪');
 $('profileName').value=config.profile_name||'';$('profileName').readOnly=!!config.profile_name;
 if(config.configuration){const c=config.configuration;$('configSources').textContent=`配置来源：${c.files.join(' → ')}\n设备：${c.device_file||'未选择'}；Python 相机编号：${c.camera.index}；去畸变：${c.camera.undistort?'开启':'关闭'}\n`+Object.entries(c.keys).map(([key,value])=>`${key} ← ${value}`).join('\n');}
 distanceConfig=config.distance_check;if(distanceConfig)$('distanceStart').textContent=`开始 ${distanceConfig.distances_cm[0]} cm 自检`;
 if(config.camera){lastCamera=config.camera;$('cameraStatus').textContent=cameraStatus(lastCamera);}
 if(config.screen_resolution&&config.screen_resolution.some((v,i)=>v!==[screen.width,screen.height][i]))$('cameraStatus').textContent+=' · 当前屏幕逻辑分辨率与设备文件不同，请核对屏幕与缩放';
 jumpConfigReady=true;buttons();}catch(error){recording.fail(error);$('jump').disabled=true;}}
$('compareDisplay').checked=false;
loadRecordingConfig();
async function poll(){try{const response=await fetch('/api/status'),status=await response.json();ready=status.ready;$('serverState').textContent=ready?'模型就绪 · 本地运行':status.error?'模型加载失败':'正在加载模型';$('serverLamp').classList.toggle('ready',ready);if(status.camera&&!running){lastCamera=status.camera;$('cameraStatus').textContent=cameraStatus(lastCamera);}if(status.error)message(`模型加载失败：${status.error}`);else if(ready&&!running&&$('message').textContent.includes('首次'))message('模型已就绪。开启摄像头，开始体验。');recording.poll();buttons();}catch{$('serverState').textContent='本地服务未连接';}setTimeout(poll,3000);}poll();animate();
