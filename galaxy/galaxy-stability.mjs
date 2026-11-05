// Offline/preview diagnostics: compare actual positions, not rendered brightness.
const wrap=a=>Math.atan2(Math.sin(a),Math.cos(a));
function pitchFit(positions,time,o){
 const k=1/Math.tan(o.galaxy_pitch_deg*Math.PI/180),r0=o.galaxy_radius*.15;
 let n=0,sx=0,sy=0,sxx=0,sxy=0;
 for(let j=0;j<positions.length;j+=3){const r=Math.hypot(positions[j],positions[j+2]),x=Math.log(r/r0),angle=Math.atan2(positions[j+2],positions[j])+time*o.galaxy_spin;
  // Fold the two arms, resolving phase around the configured log spiral.
  const y=k*x+wrap(2*(angle-k*x))/2;n++;sx+=x;sy+=y;sxx+=x*x;sxy+=x*y;}
 const slope=(sxy-sx*sy/n)/(sxx-sx*sx/n);return Math.atan(1/slope)*180/Math.PI;
}
export function compareArms(reference,positions,t0,time,o){
 if(reference.length!==positions.length||positions.length<300)throw new Error('旋臂检查样本不足或不对应');
 const k=1/Math.tan(o.galaxy_pitch_deg*Math.PI/180),deltas=[],bins=Array.from({length:8},()=>({x:0,y:0,n:0}));
 for(let j=0;j<positions.length;j+=3){
  const r0=Math.hypot(reference[j],reference[j+2]),r=Math.hypot(positions[j],positions[j+2]);
  const d=wrap(Math.atan2(positions[j+2],positions[j])-Math.atan2(reference[j+2],reference[j])+o.galaxy_spin*(time-t0)-k*Math.log(r/r0));
  deltas.push(d*180/Math.PI);const bin=bins[Math.min(7,Math.floor(r0/o.galaxy_radius*8))];bin.x+=Math.cos(d);bin.y+=Math.sin(d);bin.n++;
 }
 const angleBins=bins.filter(b=>b.n>10).map(b=>Math.atan2(b.y,b.x)*180/Math.PI),rms=Math.sqrt(deltas.reduce((s,v)=>s+v*v,0)/deltas.length),basePitch=pitchFit(reference,t0,o),pitch=pitchFit(positions,time,o);
 const tolerance=o.galaxy_stability_tolerance_deg,maxBin=Math.max(...angleBins.map(Math.abs));
 return {time_s:time,sample_count:deltas.length,fitted_pitch_deg:pitch,pitch_change_deg:Math.abs(pitch-basePitch),phase_rms_deg:rms,max_bin_angle_change_deg:maxBin,bin_angle_changes_deg:angleBins,tolerance_deg:tolerance,passed:rms<=tolerance&&maxBin<=tolerance&&Math.abs(pitch-basePitch)<=tolerance};
}
