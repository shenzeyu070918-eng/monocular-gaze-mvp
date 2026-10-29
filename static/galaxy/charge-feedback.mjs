// Display-only envelope. Never creates dwell evidence or changes zoom decisions.
const clamp=v=>Math.max(0,Math.min(1,v));
export class ChargeFeedback{
 constructor(options){this.o=options;this.reset();}
 reset(){this.level=0;this.phase=0;}
 step(progress,dt){
  const target=clamp(progress),seconds=Math.max(0,dt),tau=target>this.level?this.o.charge_response_s:this.o.charge_release_s;
  const before=this.level;this.level+=(target-this.level)*(1-Math.exp(-seconds/tau));
  // Integrate added angular speed. Multiplying time by a changing progress would jump.
  this.phase+=(before+this.level)*.5*seconds;
  return this.level;
 }
}
