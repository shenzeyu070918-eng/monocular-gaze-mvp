// Visual interpolation follows Python's revision/timing; completion needs its ACK.
const clamp=x=>Math.max(0,Math.min(1,x)),smooth=x=>{x=clamp(x);return x*x*(3-2*x);};
export class AssemblyTransition{
 constructor(o){this.out=o.transition_scatter_s*1000;this.in=o.transition_s*1000;this.reset(0);}
 reset(pattern=0){this.pattern=pattern;this.from=pattern;this.revision=-1;this.start=0;this.active=false;this.external=false;}
 accept(s,now){
  if(s.scene_revision<this.revision)return;
  if(s.scene_revision===this.revision&&!this.active&&this.external&&s.scene_transition)return;
  const age=s.scene_revision===this.revision&&this.active?Math.max(0,now-this.start):0;
  this.from=s.scene_from??s.pattern;this.pattern=s.pattern;this.revision=s.scene_revision;this.external=true;
  this.active=!!s.scene_transition;this.start=now-Math.max(age,s.scene_transition_ms??0);
 }
 request(pattern,now){
  if(this.active||pattern===this.pattern)return;
  this.from=this.pattern;this.pattern=pattern;this.start=now;this.active=true;this.revision++;this.external=false;
 }
 sample(now){
  const age=Math.max(0,now-this.start),ready=age>=this.out+this.in;
  if(this.active&&ready&&!this.external)this.active=false;
  const old=this.active&&age<this.out;
  return {pattern:old?this.from:this.pattern,assembly:this.active?(old?1-smooth(age/this.out):smooth((age-this.out)/this.in)):1,
   busy:this.active,ready:this.active&&ready,revision:this.revision,external:this.external,elapsed_ms:age};
 }
}
