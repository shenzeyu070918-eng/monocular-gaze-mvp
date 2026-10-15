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
 let time=2000,animation,hangReceipt=false,holdNextFrame=false,releaseFrame,timerId=0,cameraStarts=0;
 const requests=[],handlers={},timers=new Map();
 const context=new Proxy({}, {get:()=>()=>{}});
 const elements=new Map();
 function element(id){if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,checked:false,value:'',style:{},options:[{},{}],classList:{toggle(){}},getContext:()=>context,getBoundingClientRect:()=>({left:100,top:80,width:600,height:400}),addEventListener(){},checkValidity:()=>true,videoWidth:640,videoHeight:480,play:async()=>{},toDataURL:()=> 'data:image/jpeg;base64,FAKE_TRANSIENT_IMAGE'});return elements.get(id);}
 for(const [id,value]of Object.entries({widthCm:30.2,heightCm:19.6,topCm:.2,focalScale:1}))element(id).value=String(value);
 const originals={setTimeout,clearTimeout,fetch,performance,document:globalThis.document,window:globalThis.window,navigator:globalThis.navigator};
 Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>time,timeOrigin:0}});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async()=>{cameraStarts++;return {getTracks:()=>[{stop(){}}]};}}}});
 globalThis.screen={width:1000,height:800};globalThis.innerWidth=1000;globalThis.innerHeight=800;globalThis.outerWidth=1000;globalThis.outerHeight=800;globalThis.screenX=0;globalThis.screenY=0;globalThis.devicePixelRatio=2;
 globalThis.document={fullscreenElement:null,documentElement:{},getElementById:element,createElement:()=>element('capture'),addEventListener:(type,handler)=>{handlers[type]=handler;}};
 globalThis.window={addEventListener:(type,handler)=>{handlers[type]=handler;}};
 globalThis.requestAnimationFrame=callback=>{animation=callback;};globalThis.setTimeout=(callback,delay)=>{timers.set(++timerId,{callback,delay});return timerId;};globalThis.clearTimeout=id=>timers.delete(id);
 globalThis.fetch=async(path,options={})=>{const body=options.body?JSON.parse(options.body):undefined;requests.push({path,body});let result={error:null};if(path==='/api/status')result={ready:true};else if(path==='/api/recording/config')result={request_timeout_ms:250,jump:{point_count:8,dwell_ms:100,min_distance:.35}};else if(path==='/api/recording/start')result={session_id:`page-${requests.length}`,active:true};else if(path==='/api/frame')result={valid:true,raw:[.4,.5],status:'tracking',distance_cm:50,inference_ms:1};if(path.endsWith('/receipt')&&hangReceipt)return new Promise((resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('fake request aborted','AbortError')));});if(path==='/api/frame'&&holdNextFrame){holdNextFrame=false;return new Promise(resolve=>{releaseFrame=()=>resolve({ok:true,json:async()=>result});});}return {ok:true,json:async()=>result};};
 const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
 try{
  await import('../static/app.js?mock-browser');await flush();
  await element('start').onclick();await flush();
  const frame=requests.find(r=>r.path==='/api/frame').body;
  assert.equal(frame.browser_capture_ms,2000);assert.equal(frame.browser_request_ms,2000);assert.equal(frame.page_clock_id.length>0,true);
  assert.equal(element('jumpCount').value,8);
  await element('jump').onclick();assert.match(element('message').textContent,/全屏/);assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,0);
  document.fullscreenElement=document.documentElement;
  await element('jump').onclick();animation();await flush();
  const first=requests.find(r=>r.body?.type==='target').body;
  assert.equal(first.action,'appear');assert.equal(first.task_type,'jump');assert.equal(first.stage_target,null);assert.ok([0,.5,1].includes(first.target[0]));
  assert.equal(element('jumpOverlay').hidden,false);
  time+=101;animation();await flush();
  const targets=requests.filter(r=>r.body?.type==='target');assert.equal(targets.at(-1).body.action,'switch');assert.ok(Math.hypot(...first.target.map((v,i)=>v-targets.at(-1).body.target[i]))>=.35);
  assert.equal(element('export').disabled,true);assert.equal(requests.some(r=>r.path==='/api/calibrate'),false);
  document.fullscreenElement=null;handlers.fullscreenchange();await flush();
  assert.equal(requests.filter(r=>r.body?.type==='target').at(-1).body.reason,'fullscreen_exited');
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,1);
  element('recording').checked=true;await element('recording').onchange();
  const starts=requests.filter(r=>r.path.endsWith('/start')).length;
  await element('evaluate').onclick();animation();await flush();element('cancel').onclick();await flush();
  assert.equal(requests.filter(r=>r.path.endsWith('/start')).length,starts);
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,1);
  element('recording').checked=false;await element('recording').onchange();
  assert.equal(requests.filter(r=>r.path.endsWith('/stop')).length,2);
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
 }finally{globalThis.setTimeout=originals.setTimeout;globalThis.clearTimeout=originals.clearTimeout;globalThis.fetch=originals.fetch;Object.defineProperty(globalThis,'performance',{configurable:true,value:originals.performance});Object.defineProperty(globalThis,'navigator',{configurable:true,value:originals.navigator});globalThis.document=originals.document;globalThis.window=originals.window;}
 console.log('page fake-camera test passed: fullscreen gate, timing payload, jump isolation, manual-session reuse, camera-pause late ACK and hung-receipt timeout');
}
