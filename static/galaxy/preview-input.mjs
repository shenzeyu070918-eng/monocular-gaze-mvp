// Synthetic values for the camera-free preview only. Same display controls as live frames.
export function previewFrame(captureUs,size,pattern,gaze=null,hand='none'){
 const hands=hand==='none'?[]:[[.5,.5,hand==='open'?1:0,0,0,0,hand==='open'?1:-1]];
 return {capture_us:captureUs,intro:0,pattern,gaze_valid:!!gaze,xy:gaze??[size[0]/2,size[1]/2],
  radius:[size[0]*.035,size[1]*.055],control:Number(hands.length>0),hand_available:1,hand_error:0,hands,distance:0};
}
export class PreviewClock{
 constructor(){this.reset();}
 reset(){this.last=null;this.frame=null;}
 sample(now,size,pattern,gaze,hand){
  // 15 Hz synthetic input, independent of the browser's rendering cadence.
  if(this.last===null||now-this.last>=1000/15){this.last=now;this.frame=previewFrame(Math.round(now*1000),size,pattern,gaze,hand);}
  return this.frame;
 }
}
