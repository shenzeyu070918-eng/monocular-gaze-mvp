// Visual envelope only. Closed-eye evidence is counted exclusively in Python.
const smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x);};
export class BurstEnvelope{
 constructor(o){this.o=o.interaction;this.reset();}
 reset(){this.revision=-1;this.active=false;this.start=0;}
 accept(s,now){
  if(!s||!Number.isInteger(s.burst_revision)||s.burst_revision<this.revision)return;
  if(s.burst_revision===this.revision&&!this.active&&s.burst_active)return;
  const elapsed=s.burst_revision===this.revision&&this.active?Math.max(0,now-this.start):0;
  this.revision=s.burst_revision;this.active=!!s.burst_active;this.start=now-Math.max(elapsed,s.burst_elapsed_ms??0);
 }
 cancel(){this.active=false;}
 trigger(now){this.accept({burst_revision:this.revision+1,burst_active:1,burst_elapsed_ms:0},now);}
 value(now){
  if(!this.active)return 0;const o=this.o,t=Math.max(0,now-this.start);
  if(t<o.burst_attack_ms)return smooth(t/o.burst_attack_ms);
  if(t<o.burst_attack_ms+o.burst_hold_ms)return 1;
  return 1-smooth((t-o.burst_attack_ms-o.burst_hold_ms)/(o.burst_duration_ms-o.burst_attack_ms-o.burst_hold_ms));
 }
}
const reasons=['就绪/已触发','闭眼时间不足','丢脸已取消','采集断档已取消','冷却中','开场/汇聚中','需先睁眼','状态无效'];
export function burstDiagnostic(s){return '闭眼 '+(s?.blink_closed_ms??0).toFixed(0)+' ms · 闭眼触发 '+(s?.blink_count??0)+' / 总打散 '+(s?.burst_revision??0)+' · 最近未触发原因：'+(reasons[s?.blink_reason]??reasons[6]);}
