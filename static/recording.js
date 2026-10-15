// Recording has one owner; frame completion is retained even after the camera stops.
export function drawCapture(ctx, video, canvas, now = () => performance.now()) {
 ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
 return now();
}
export function videoFrameTiming(metadata) {
 if (!metadata) return {source:'drawImage'};
 const result = {source:'requestVideoFrameCallback_latest'};
 for (const key of ['callback_ms','presentationTime','expectedDisplayTime','mediaTime','presentedFrames','captureTime','receiveTime','processingDuration','width','height']) {
  if (Number.isFinite(metadata[key])) result[key] = metadata[key];
 }
 return result;
}
export class RecordingSession {
 constructor({request, onState = () => {}, onError = () => {}, now = () => performance.now()}) {
  this.request=request;this.onState=onState;this.onError=onError;this.now=now;
  this.session=null;this.manual=false;this.taskId=null;this.busy=false;this.closing=false;this.stopping=null;this.pending=new Set();this.error=null;
 }
 state(){return {active:!!this.session,manual:this.manual,taskId:this.taskId,busy:this.busy,closing:this.closing,session:this.session,error:this.error};}
 changed(){this.onState(this.state());}
 fail(error){this.error=error instanceof Error?error.message:String(error);if(this.session&&!this.closing)this.stop('recording_failed');this.onError(this.error);this.changed();}
 async send(path, body){const value=await this.request(path,body);if(value?.error)throw Error(value.error);return value;}
 track(promise){const tracked=Promise.resolve(promise).catch(error=>{this.fail(error);return null;});this.pending.add(tracked);tracked.finally(()=>this.pending.delete(tracked));return tracked;}
 frameContext(){return this.session&&!this.closing?{session_id:this.session.session_id}:null;}
 async open(metadata,calibration){
  const result=await this.send('/api/recording/start',metadata);
  try{await this.send('/api/recording/event',{session_id:result.session_id,type:'calibration',...calibration,browser_time_ms:this.now()});}
  catch(error){try{await this.send('/api/recording/stop',{session_id:result.session_id,reason:'initial_calibration_failed',browser_time_ms:this.now()});}catch(cleanupError){this.fail(cleanupError);}throw error;}
  // Frames are admitted only after the initial calibration state is accepted.
  this.error=null;this.session=result;this.changed();
 }
 async startManual(metadata,calibration){
  if(this.busy||this.taskId||this.session)throw Error('录制会话已开启或正在切换。');
  this.busy=true;this.changed();
  try{await this.open(metadata,calibration);this.manual=true;}catch(error){this.fail(error);throw error;}finally{this.busy=false;this.changed();}
 }
 async beginTask(taskId,metadata,calibration){
  if(this.busy||this.taskId||this.closing)throw Error('请等待当前录制任务结束。');
  this.busy=true;this.changed();
  try{if(!this.session)await this.open(metadata,calibration);this.taskId=taskId;}catch(error){this.fail(error);throw error;}finally{this.busy=false;this.changed();}
 }
 event(event){
  if(!this.session||this.closing)return Promise.resolve(null);
  return this.track(this.send('/api/recording/event',{session_id:this.session.session_id,...event}));
 }
 receipt(context,frameId,responseTime){return this.send('/api/recording/receipt',{session_id:context.session_id,frame_id:frameId,browser_response_ms:responseTime});}
 async endTask(taskId,reason){
  if(this.taskId!==taskId)return;
  this.taskId=null;this.changed();
  if(!this.manual)await this.stop(reason);
 }
 stop(reason='manual'){
  if(this.closing)return this.stopping;
  if(!this.session)return Promise.resolve();
  const session=this.session;
  // Uncheck immediately, while preserving frozen frame contexts for cleanup.
  this.session=null;this.manual=false;this.closing=true;this.busy=true;this.changed();
  this.stopping=this.finishStop(session,reason);
  return this.stopping;
 }
 async finishStop(session,reason){
  try{
   // No new frame contexts are issued while closing. Previously started frames
   // retain their session ID and complete their receipt before the end record.
   while(this.pending.size)await Promise.allSettled([...this.pending]);
   await this.send('/api/recording/stop',{session_id:session.session_id,reason,browser_time_ms:this.now()});
  }catch(error){this.fail(error);}finally{if(reason!=='recording_failed')this.taskId=null;this.closing=false;this.busy=false;this.stopping=null;this.changed();}
 }
 async poll(){
  if(!this.session||this.busy)return;
  const session=this.session;
  try{const status=await this.request('/api/recording/status');if(this.session!==session)return;if(status.error)this.fail(status.error);if(status.error||status.session_id!==session.session_id||!status.active){if(!status.error)this.fail('录制已停止，请检查录制文件和本地服务。');this.session=null;this.manual=false;this.changed();}}
  catch(error){this.fail(error);}
 }
}
export function stagePointToScreen(point, screen) {
 let x=0,y=0;
 if(!screen.fullscreen){const border=Math.max(0,(screen.outer_width_px-screen.window_width_px)/2);x=screen.screen_x+border;y=screen.screen_y+Math.max(0,screen.outer_height_px-screen.window_height_px-border);}
 return [(x+screen.stage_left_px+point[0]*screen.stage_width_px)/screen.width_px,(y+screen.stage_top_px+point[1]*screen.stage_height_px)/screen.height_px];
}
