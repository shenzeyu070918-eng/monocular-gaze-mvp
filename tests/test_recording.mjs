import assert from 'node:assert/strict';
import {RecordingSession,drawCapture,videoFrameTiming,stagePointToScreen} from '../static/recording.js';
const calls=[];
const context={drawImage(){calls.push('drawImage');}};
const canvas={width:640,height:480};
assert.equal(drawCapture(context,{},canvas,()=>{calls.push('clock');return 17;}),17);
assert.deepEqual(calls,['drawImage','clock']);
assert.deepEqual(videoFrameTiming(null),{source:'drawImage'});
assert.deepEqual(videoFrameTiming({callback_ms:21,mediaTime:.3,presentationTime:20,unexpected:'image',captureTime:undefined}),{source:'requestVideoFrameCallback_latest',callback_ms:21,presentationTime:20,mediaTime:.3});
assert.deepEqual(stagePointToScreen([.5,.5],{width_px:1000,height_px:800,fullscreen:true,stage_left_px:100,stage_top_px:80,stage_width_px:600,stage_height_px:400}),[.4,.35]);
assert.deepEqual(stagePointToScreen([0,0],{width_px:1000,height_px:800,fullscreen:false,screen_x:10,screen_y:20,outer_width_px:820,window_width_px:800,outer_height_px:650,window_height_px:600,stage_left_px:100,stage_top_px:80,stage_width_px:600,stage_height_px:400}),[.12,.175]);
function fixture(){const requests=[],errors=[];let starts=0;const recording=new RecordingSession({now:()=>100,request:async(path,body)=>{requests.push({path,body});if(path.endsWith('/start'))return {session_id:`s${++starts}`,active:true};if(path.endsWith('/status'))return {session_id:`s${starts}`,active:true};return {error:null};},onError:error=>errors.push(error)});return {recording,requests,errors};}
const meta={page_clock_id:'page1'},calibration={action:'reset',matrix:null,calibration_id:'none',group:'A'};
{
 const {recording,requests}=fixture();
 await recording.startManual(meta,calibration);
 assert.equal(recording.state().manual,true);
 await recording.beginTask('task1',meta,calibration);
 await assert.rejects(recording.beginTask('task2',meta,calibration),/请等待/);
 await assert.rejects(recording.startManual(meta,calibration),/已开启/);
 await recording.event({type:'target',task_id:'task1',target:[0,1],browser_time_ms:110});
 await recording.endTask('task1','completed');
 assert.equal(recording.state().active,true);
 assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,1);
 assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,0);
 await recording.stop('manual');
 assert.equal(recording.state().active,false);
 assert.equal(requests.at(-1).body.reason,'manual');
}
{
 const {recording,requests}=fixture();
 await recording.beginTask('auto',meta,calibration);
 const captured=recording.frameContext();
 let finishInference;
 const frame=recording.track(new Promise(resolve=>{finishInference=resolve;}).then(()=>recording.receipt(captured,9,145)));
 const closing=recording.endTask('auto','camera_paused');
 assert.equal(recording.frameContext(),null);
 assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,0);
 finishInference();await frame;await closing;
 const receipt=requests.findIndex(r=>r.path.endsWith('/receipt')),stop=requests.findIndex(r=>r.path.endsWith('/stop'));
 assert.ok(receipt>=0&&stop>receipt);
 assert.deepEqual(requests[receipt].body,{session_id:'s1',frame_id:9,browser_response_ms:145});
 assert.equal(recording.state().active,false);
 assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,1);
}
{
 const {recording,requests}=fixture();
 await recording.beginTask('calibration',meta,calibration);
 let releaseEvent;
 recording.track(new Promise(resolve=>{releaseEvent=resolve;}).then(()=>recording.send('/api/recording/event',{type:'calibration',action:'applied'})));
 const stopping=recording.endTask('calibration','completed');
 assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,0);
 releaseEvent();await stopping;
 assert.equal(requests.at(-2).body.action,'applied');
 assert.equal(requests.at(-1).body.reason,'completed');
}
{
 const {recording,errors}=fixture();await recording.startManual(meta,calibration);
 await recording.track(Promise.reject(Error('磁盘写入失败')));
 assert.deepEqual(errors,['磁盘写入失败']);
 assert.equal(recording.state().error,'磁盘写入失败');
 await recording.stop();
 assert.equal(recording.state().error,'磁盘写入失败');
}
{
 const states=[],requests=[];let finishStop;
 const recording=new RecordingSession({onState:state=>states.push(state),request:async(path,body)=>{
  requests.push({path,body});
  if(path.endsWith('/start'))return {session_id:'invalid-result',active:true};
  if(path.endsWith('/receipt'))return {active:false,error:'录制结果格式无效：raw 必须是有限数值'};
  if(path.endsWith('/stop'))return new Promise(resolve=>{finishStop=()=>resolve({active:false,error:null});});
  return {error:null};
 }});
 await recording.startManual(meta,calibration);
 const context=recording.frameContext();
 await recording.track(recording.receipt(context,1,130));
 assert.equal(recording.state().active,false);
 assert.equal(recording.state().manual,false);
 assert.equal(recording.frameContext(),null);
 assert.equal(states.at(-1).active,false);
 assert.equal(recording.state().busy,true);
 for(let i=0;i<5;i++)await Promise.resolve();
 assert.equal(requests.at(-1).path,'/api/recording/stop');
 finishStop();await recording.stop();
 assert.equal(recording.state().busy,false);
 assert.match(recording.state().error,/raw 必须是有限数值/);
}
{
 let finishStart;const recording=new RecordingSession({request:path=>path.endsWith('/start')?new Promise(resolve=>{finishStart=()=>resolve({session_id:'late',active:true});}):Promise.resolve({error:null})});
 const opening=recording.beginTask('t1',meta,calibration);
 await assert.rejects(recording.beginTask('t2',meta,calibration),/请等待/);
 finishStart();await opening;await recording.endTask('t1','cancelled');assert.equal(recording.state().active,false);
}
{
 const {recording,errors}=fixture();await recording.startManual(meta,calibration);await recording.beginTask('active-task',meta,calibration);
 const original=recording.request;recording.request=async(path,body)=>path.endsWith('/status')?{session_id:'s1',active:false,error:'后台磁盘错误'}:original(path,body);
 await recording.poll();assert.equal(recording.state().active,false);assert.equal(recording.state().manual,false);assert.equal(recording.state().taskId,'active-task');assert.deepEqual(errors,['后台磁盘错误']);
 await assert.rejects(recording.startManual(meta,calibration),/已开启/);await recording.endTask('active-task','completed');assert.equal(recording.state().taskId,null);
}
{
 const requests=[],errors=[];let rejectInitial;
 const recording=new RecordingSession({request:async(path,body)=>{requests.push({path,body});if(path.endsWith('/start'))return {session_id:'initial-failure',active:true};if(path.endsWith('/event'))return new Promise(resolve=>{rejectInitial=()=>resolve({error:'初始校准记录失败'});});return {active:false,error:null};},onError:error=>errors.push(error)});
 const opening=recording.startManual(meta,calibration);for(let i=0;i<5;i++)await Promise.resolve();assert.equal(recording.frameContext(),null);rejectInitial();await assert.rejects(opening,/初始校准记录失败/);
 assert.equal(recording.state().active,false);assert.equal(recording.state().manual,false);assert.equal(requests.at(-1).path,'/api/recording/stop');assert.equal(requests.at(-1).body.reason,'initial_calibration_failed');assert.ok(errors.includes('初始校准记录失败'));
}
console.log('recording timing, coordinates, ownership, draining and error tests passed');
// Exercise the actual page handlers with a fake DOM and camera. No device,
// calibration fitting or real-time measurements are involved.
{
 let time=2000,drawCost=0,frameCost=0,animation,hangReceipt=false,failReceipt=false,holdNextFrame=false,failProfile=false,releaseFrame,timerId=0,cameraStarts=0;
 const requests=[],handlers={},timers=new Map(),rootClasses=new Set();
 const context=new Proxy({}, {get:(_,key)=>key==='drawImage'?()=>{time+=drawCost;}:()=>{}});
 const elements=new Map();
 function element(id){if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,checked:false,value:'',style:{},options:[{},{}],classList:{toggle(){}},getContext:()=>context,getBoundingClientRect:()=>({left:100,top:80,width:600,height:400}),addEventListener(){},checkValidity:()=>true,click(){},videoWidth:640,videoHeight:480,play:async()=>{},toDataURL:()=> 'data:image/jpeg;base64,FAKE_TRANSIENT_IMAGE'});return elements.get(id);}
 for(const [id,value]of Object.entries({widthCm:30.2,heightCm:19.6,topCm:.2,focalScale:1}))element(id).value=String(value);
 let exportedCsv;const createObjectURL=URL.createObjectURL,revokeObjectURL=URL.revokeObjectURL;URL.createObjectURL=blob=>{exportedCsv=blob.text();return 'blob:synthetic';};URL.revokeObjectURL=()=>{};
 const originals={setTimeout,clearTimeout,fetch,performance,document:globalThis.document,window:globalThis.window,navigator:globalThis.navigator,getComputedStyle:globalThis.getComputedStyle};
 Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>time,timeOrigin:0}});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async()=>{cameraStarts++;return {getTracks:()=>[{stop(){}}]};}}}});
 globalThis.screen={width:1000,height:800};globalThis.innerWidth=1000;globalThis.innerHeight=800;globalThis.outerWidth=1000;globalThis.outerHeight=800;globalThis.screenX=0;globalThis.screenY=0;globalThis.devicePixelRatio=2;
 globalThis.document={fullscreenElement:null,documentElement:{classList:{add:name=>rootClasses.add(name),remove:name=>rootClasses.delete(name)}},getElementById:element,createElement:()=>element('capture'),addEventListener:(type,handler)=>{handlers[type]=handler;}};
 globalThis.getComputedStyle=el=>el===element('jumpTarget')?{width:'32px',height:'32px'}:{width:'72px',height:'20px'};
 globalThis.window={addEventListener:(type,handler)=>{handlers[type]=handler;}};
 globalThis.requestAnimationFrame=callback=>{animation=callback;};globalThis.setTimeout=(callback,delay)=>{timers.set(++timerId,{callback,delay});return timerId;};globalThis.clearTimeout=id=>timers.delete(id);
 globalThis.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});let result={error:null};if(path==='/api/status')result={ready:true};else if(path==='/api/recording/config')result={request_timeout_ms:250,jump:{point_count:8,dwell_ms:100,min_distance:.35,margin:.05},evaluation:{center_points:[[.25,.25],[.75,.75]],calibration_points:[[.5,.5],[.12,.12],[.5,.12],[.88,.12],[.88,.5],[.88,.88],[.5,.88],[.12,.88],[.12,.5]],settle_ms:650,dwell_ms:2200,max_dwell_ms:10000,calibration_min_samples:8},profile_name:'synthetic',distance_check:{distances_cm:[40,50,60],settle_ms:10,duration_ms:100,min_samples:1,warning_cm:5}};else if(path==='/api/calibrate')result={matrix:[[1,0],[0,1],[0,0]],profile_file:failProfile?null:'synthetic.json',profile_error:failProfile?'disk full':null};else if(path==='/api/evaluation/summary')result={groups:{A:{all:{valid_targets:1,expected_targets:10,angle_target_count:0,mean_cm:null,p90_cm:null,within15:null,within2:null,horizontal_cm:null,vertical_cm:null,angle_deg:null,yaw_deg:null,pitch_deg:null,horizontal_scale:null,vertical_scale:null,angle_frame_count:0,missing_origin_frames:1,invalid_origin_frames:0},center:{},edge:{}}}};else if(path==='/api/recording/start')result={session_id:`page-${requests.length}`,active:true};else if(path==='/api/frame'){time+=frameCost;result={valid:true,raw:[.4,.5],status:'tracking',distance_cm:50,inference_ms:1,camera:{intrinsics_source:'estimated',device_file:'synthetic.yaml',intrinsics_reason:'aspect_ratio_mismatch',undistort_enabled:true,undistort_applied:false,scaled:false,width_px:640,height_px:480}};}else if(path.endsWith('/receipt')&&failReceipt)result={active:false,error:'录制结果格式无效：raw 必须是有限数值'};if(path.endsWith('/receipt')&&hangReceipt)return new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('fake request aborted','AbortError')));});if(path==='/api/frame'&&holdNextFrame){holdNextFrame=false;return new Promise(resolve=>{releaseFrame=()=>resolve({ok:true,json:async()=>result});});}return {ok:true,json:async()=>result};};
 const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
 try{
  await import('../static/app.js?mock-browser');await flush();
  await element('start').onclick();await flush();
  const frame=requests.find(r=>r.path==='/api/frame').body;
  assert.equal(frame.browser_capture_ms,2000);assert.equal(frame.browser_request_ms,2000);assert.equal(frame.page_clock_id.length>0,true);
  // Synthetic clock costs: include drawing in pacing, but timestamp capture
  // after drawing. None of these inputs are real camera/performance results.
  function loopWait(){return [...timers.entries()].filter(([,timer])=>timer.delay<=33).at(-1);}
  async function nextFrame(){const [id,timer]=loopWait();timers.delete(id);time+=timer.delay;const start=time;timer.callback();await flush();return start;}
  drawCost=4;frameCost=7;
  const loopStart=await nextFrame(),paced=requests.filter(r=>r.path==='/api/frame').at(-1).body;
  assert.equal(paced.browser_capture_ms,loopStart+4);
  assert.equal(paced.browser_request_ms,loopStart+4);
  assert.equal(loopWait()[1].delay,22);
  const followingStart=await nextFrame(),following=requests.filter(r=>r.path==='/api/frame').at(-1).body;
  assert.equal(followingStart-loopStart,33);
  assert.equal(following.browser_capture_ms-paced.browser_capture_ms,33);
  assert.equal(following.browser_capture_ms,followingStart+4);
  drawCost=20;frameCost=20;
  const slowStart=await nextFrame();
  assert.equal(requests.filter(r=>r.path==='/api/frame').at(-1).body.browser_capture_ms,slowStart+20);
  assert.equal(loopWait()[1].delay,0);
  drawCost=0;frameCost=0;await nextFrame();
  assert.equal(element('jumpCount').value,8);
  assert.equal(element('jumpMargin').value,5);
  await element('jump').onclick();assert.match(element('message').textContent,/全屏/);assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,0);
  document.fullscreenElement=document.documentElement;
  element('jumpMargin').value=.1;await element('jump').onclick();assert.match(element('message').textContent,/完整显示目标圆/);assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,0);
  element('jumpMargin').value=40;await element('jump').onclick();assert.match(element('message').textContent,/最小间距/);assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,0);
  element('jumpMargin').value=5;await element('jump').onclick();
  assert.equal(requests.some(r=>r.body?.type==='target'),false);
  animation();await flush();
  const first=requests.find(r=>r.body?.type==='target').body;
  assert.equal(first.action,'appear');assert.equal(first.task_type,'jump');assert.equal(first.stage_target,null);assert.ok([.05,.5,.95].includes(first.target[0]));
  assert.equal(first.browser_time_ms,time);
  assert.deepEqual(first.target,[parseFloat(element('jumpTarget').style.left)/100,parseFloat(element('jumpTarget').style.top)/100]);
  assert.equal(element('jumpOverlay').hidden,false);
  assert.equal(element('jumpEscape').hidden,false);assert.equal(rootClasses.has('jump-active'),true);
  time+=101;animation();await flush();
  const targets=requests.filter(r=>r.body?.type==='target');assert.equal(targets.at(-1).body.action,'switch');assert.ok(Math.hypot(...first.target.map((v,i)=>v-targets.at(-1).body.target[i]))>=.35);
  assert.deepEqual(targets.at(-1).body.target,[parseFloat(element('jumpTarget').style.left)/100,parseFloat(element('jumpTarget').style.top)/100]);
  assert.equal(targets.at(-1).body.browser_time_ms,time);
  assert.equal(element('export').disabled,true);assert.equal(requests.some(r=>r.path==='/api/calibrate'),false);
  document.fullscreenElement=null;handlers.fullscreenchange();await flush();
  assert.equal(requests.filter(r=>r.body?.type==='target').at(-1).body.reason,'fullscreen_exited');
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,1);
  assert.equal(rootClasses.has('jump-active'),false);
  document.fullscreenElement=document.documentElement;globalThis.innerWidth=640;globalThis.innerHeight=480;
  await element('jump').onclick();animation();await flush();
  assert.equal(element('jumpEscape').hidden,true);
  handlers.keydown({key:'Escape'});await flush();
  assert.equal(requests.filter(r=>r.body?.type==='target').at(-1).body.reason,'cancelled');
  assert.equal(element('jumpOverlay').hidden,true);assert.equal(rootClasses.has('jump-active'),false);
  document.fullscreenElement=null;globalThis.innerWidth=1000;globalThis.innerHeight=800;
  await element('evaluate').onclick();assert.match(element('message').textContent,/全屏/);await element('calibrate').onclick();assert.match(element('message').textContent,/全屏/);document.fullscreenElement=document.documentElement;
  const baselineStops=requests.filter(r=>r.path.endsWith('/stop')).length;
  element('recording').checked=true;await element('recording').onchange();
  const starts=requests.filter(r=>r.path.endsWith('/start')).length;
  await element('evaluate').onclick();animation();await flush();time+=700;await nextFrame();
  const firstEvaluationTarget=requests.filter(r=>r.body?.type==='target').at(-1).body.target;
  holdNextFrame=true;await nextFrame();const summaryCount=requests.filter(r=>r.path==='/api/evaluation/summary').length;
  time+=2300;animation();await flush();element('cancel').onclick();await flush();
  assert.equal(requests.filter(r=>r.path==='/api/evaluation/summary').length,summaryCount);releaseFrame();await flush();
  const evaluation=requests.filter(r=>r.path==='/api/evaluation/summary').at(-1).body;
  assert.equal(evaluation.completed,false);assert.equal(evaluation.frames.length,2);assert.equal(evaluation.frames[0].target_id,evaluation.frames[1].target_id);
  assert.deepEqual(evaluation.targets.find(t=>t.target_id===evaluation.frames[0].target_id).target,firstEvaluationTarget);
  assert.equal(evaluation.targets.filter(t=>t.region==='edge').length,8);
  element('export').onclick();const csv=(await exportedCsv).replace(/^\uFEFF/,'').split('\n');
  const legacy=['run_id','calibration_id','capture_time_ms','valid','status','grayscale','geometry_estimated','physical_dimensions_confirmed','fullscreen','stage_width_px','stage_height_px','screen_width_cm','screen_height_cm','target_x','target_y','raw_screen_x','raw_screen_y','uncalibrated_x','uncalibrated_y','calibrated_x','calibrated_y','error_a_px','error_b_px','error_a_cm','error_b_cm','inference_ms'];
  const keys=csv[0].split(',');const row=csv[1].split(',');assert.ok(Math.abs((+row[keys.indexOf('target_x')]*600+100)/1000-(+row[keys.indexOf('target_screen_x')]))<1e-12);assert.deepEqual(keys.slice(0,legacy.length),legacy);
  assert.deepEqual(keys.slice(legacy.length,34),['intrinsics_source','device_file','intrinsics_reason','undistort_enabled','undistort_applied','intrinsics_scaled','camera_width_px','camera_height_px']);assert.deepEqual(keys.slice(34,36),['region','target_id']);assert.equal(csv[1].split(',')[keys.indexOf('intrinsics_reason')],'aspect_ratio_mismatch');
  assert.equal(csv[1].split(',')[keys.indexOf('error_a_cm')],''); // No measured dimensions: no cm result.
  const beforeDistanceStarts=requests.filter(r=>r.path.endsWith('/start')).length;
  for(const measured of [40,50,60]){
   await element('distanceStart').onclick();await flush();time+=11;await nextFrame();time+=100;animation();await flush();
   const result=requests.filter(r=>r.body?.type==='distance_check').at(-1).body;
   assert.equal(result.measured_cm,measured);assert.equal(result.estimated_cm,50);assert.equal(result.bias_cm,50-measured);
   assert.equal(result.camera.intrinsics_reason,'aspect_ratio_mismatch');
  }
  assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,beforeDistanceStarts);
  assert.equal(element('recording').checked,true);assert.match(element('distanceHint').textContent,/自检完成/);
  assert.equal(requests.some(r=>r.path==='/api/calibrate'),false);
  assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,starts);
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,baselineStops);
  element('recording').checked=false;await element('recording').onchange();
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,baselineStops+1);
  // Self-check also owns exactly one automatic session, and cancellation
  // closes it without affecting calibration or evaluation records.
  const autoStarts=requests.filter(r=>r.path.endsWith('/start')).length,autoStops=requests.filter(r=>r.path.endsWith('/stop')).length;
  for(const measured of [40,50,60]){await element('distanceStart').onclick();await flush();time+=11;await nextFrame();time+=100;animation();await flush();}
  assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,autoStarts+1);
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,autoStops+1);
  assert.equal(element('recording').checked,false);
  await element('distanceStart').onclick();await flush();await element('distanceCancel').onclick();await flush();
  assert.equal(requests.at(-1).path,'/api/recording/stop');assert.equal(requests.at(-1).body.reason,'cancelled');
  // Calibration uses the same fullscreen overlay and cancels before fitting on exit.
  document.fullscreenElement=document.documentElement;await element('calibrate').onclick();animation();await flush();
  const calibrationTarget=requests.filter(r=>r.body?.type==='target').at(-1).body;
  assert.deepEqual(calibrationTarget.target,[.5,.5]);
  document.fullscreenElement=null;handlers.fullscreenchange();await flush();
  assert.equal(requests.some(r=>r.path==='/api/calibrate'),false);
  document.fullscreenElement=document.documentElement;await nextFrame();await element('calibrate').onclick();animation();await flush();
  for(let point=0;point<9;point++){
   time+=700;for(let sample=0;sample<8;sample++)await nextFrame();time+=1400;animation();await flush();
  }
  const fit=requests.find(r=>r.path==='/api/calibrate').body;
  assert.equal(fit.fullscreen,true);assert.equal(fit.samples.length,9);
  assert.deepEqual(fit.samples[0].target,[.5,.5]);assert.deepEqual(fit.samples[1].target,[.12,.12]);
  assert.deepEqual(fit.samples[0].raw,[.4,.5]);assert.match(element('profileStatus').textContent,/synthetic.json/);
  failProfile=true;await nextFrame();await element('calibrate').onclick();animation();await flush();
  for(let point=0;point<9;point++){time+=700;for(let sample=0;sample<8;sample++)await nextFrame();time+=1400;animation();await flush();}
  assert.match(element('profileStatus').textContent,/保存失败.*disk full/);assert.match(element('message').textContent,/档案未保存/);
  element('recording').checked=true;await element('recording').onchange();
  assert.equal(element('gray').disabled,true);assert.equal(element('widthCm').disabled,true);assert.equal(element('measured').disabled,true);
  holdNextFrame=true;const [waitId,waitTimer]=[...timers.entries()].find(([,timer])=>timer.delay===33);timers.delete(waitId);waitTimer.callback();await flush();
  assert.ok(releaseFrame);assert.ok(requests.at(-1).body.recording_session_id);
  await element('start').onclick();assert.equal(element('start').disabled,true);assert.equal(element('recording').disabled,true);
  const previousStarts=cameraStarts;await element('start').onclick();assert.equal(cameraStarts,previousStarts);
  time+=10;releaseFrame();await flush();
  const receiptIndex=requests.findLastIndex(r=>r.path.endsWith('/receipt')),stopIndex=requests.findLastIndex(r=>r.path.endsWith('/stop'));
  assert.ok(receiptIndex>=0&&stopIndex>receiptIndex);assert.equal(requests[receiptIndex].body.browser_response_ms,time);
  assert.equal(element('start').disabled,false);assert.equal(element('gray').disabled,false);
  await element('start').onclick();await flush();element('recording').checked=true;await element('recording').onchange();
  hangReceipt=true;const [nextId,nextTimer]=[...timers.entries()].filter(([,timer])=>timer.delay===33).at(-1);timers.delete(nextId);nextTimer.callback();await flush();
  assert.equal(requests.at(-1).path,'/api/recording/receipt');
  assert.equal(element('tracking').textContent,'跟踪中');
  // Slow receipts are independent; inference has already updated the page and
  // yielded to its next-frame timer, while close still retains the pending ACK.
  assert.ok([...timers.entries()].some(([id,timer])=>id>nextId&&timer.delay===33));
  await element('start').onclick();await flush();assert.equal(element('start').disabled,true);
  const [timeoutId,timeout]=[...timers.entries()].find(([,timer])=>timer.delay===250);timers.delete(timeoutId);timeout.callback();await flush();
  assert.equal(element('recordingError').hidden,false);assert.match(element('recordingError').textContent,/超时/);assert.equal(element('start').disabled,false);assert.equal(element('recording').checked,false);assert.equal(requests.at(-1).path,'/api/recording/stop');
  hangReceipt=false;await element('start').onclick();await flush();
  element('recording').checked=true;await element('recording').onchange();
  assert.equal(element('recording').checked,true);
  assert.equal(element('recordingError').hidden,true);
  failReceipt=true;
  const [failureId,failureTimer]=[...timers.entries()].filter(([,timer])=>timer.delay===33).at(-1);timers.delete(failureId);failureTimer.callback();await flush();
  assert.equal(element('recordingError').hidden,false);assert.match(element('recordingError').textContent,/raw 必须是有限数值/);
  assert.equal(element('recording').checked,false);
  assert.equal(requests.some(r=>r.path==='/api/recording/status'),false);
  assert.equal(requests.at(-1).path,'/api/recording/stop');
  assert.equal(element('tracking').textContent,'跟踪中');
  await element('start').onclick();await flush();
 }finally{URL.createObjectURL=createObjectURL;URL.revokeObjectURL=revokeObjectURL;globalThis.setTimeout=originals.setTimeout;globalThis.clearTimeout=originals.clearTimeout;globalThis.fetch=originals.fetch;Object.defineProperty(globalThis,'performance',{configurable:true,value:originals.performance});Object.defineProperty(globalThis,'navigator',{configurable:true,value:originals.navigator});globalThis.document=originals.document;globalThis.window=originals.window;globalThis.getComputedStyle=originals.getComputedStyle;}
 console.log('page fake-camera test passed: fullscreen gate, timing payload, jump isolation, manual-session reuse, camera-pause late ACK and hung-receipt timeout');
}
