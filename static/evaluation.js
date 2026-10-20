import {jumpAnchors} from './jump.js';
export function evaluationTargets(config,margin,random=Math.random){
 const calibration=config.calibration_points;
 const targets=[...config.center_points.map((target,i)=>({target_id:`center-${i+1}`,region:'center',target:[...target]})),...jumpAnchors(margin).map((target,i)=>({target_id:`edge-${i+1}`,region:'edge',target}))];
 for(const p of targets)if(calibration.some(c=>c.every((v,i)=>Math.abs(v-p.target[i])<1e-10)))throw Error('评估点与校准点重合，请检查配置。');
 for(let i=targets.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[targets[i],targets[j]]=[targets[j],targets[i]];}
 return targets;
}
export function evaluationFrames(records){return records.map(r=>({target_id:r.target_id,valid:r.valid,raw:r.valid?[r.raw_screen_x,r.raw_screen_y]:null,origin_m:[r.origin_x_m,r.origin_y_m,r.origin_z_m].every(Number.isFinite)?[r.origin_x_m,r.origin_y_m,r.origin_z_m]:null}));}
export function summaryText(result,completed){
 const lines=[completed?'逐点统计（各点等权）':'未完成的评估（各点等权；请勿作为完整成绩）'];
 const f=(v,unit='')=>v===null||v===undefined?'不可用':`${v.toFixed(3)}${unit}`;
 for(const [group,summary] of Object.entries(result.groups))for(const region of ['all','center','edge']){
  const s=summary[region];lines.push(`${group==='A'?'A 未个人校准':'B 九点校准'} · ${{all:'全部',center:'中央',edge:'边缘'}[region]}：${s.valid_targets}/${s.expected_targets} 点有厘米结果，${s.angle_target_count} 点有角度结果`);
  lines.push(`平均 ${f(s.mean_cm,' cm')}，P90 ${f(s.p90_cm,' cm')}；≤1.5 cm ${f(s.within15===null?null:s.within15*100,'%')}，≤2 cm ${f(s.within2===null?null:s.within2*100,'%')}`);
  lines.push(`水平偏移 ${f(s.horizontal_cm,' cm')}，垂直偏移 ${f(s.vertical_cm,' cm')}（右/下为正）；射线夹角 ${f(s.angle_deg,'°')}，yaw ${f(s.yaw_deg,'°')}，pitch ${f(s.pitch_deg,'°')}`);
  lines.push(`水平缩放 ${f(s.horizontal_scale)}，垂直缩放 ${f(s.vertical_scale)}；有效角度帧 ${s.angle_frame_count}，缺 origin ${s.missing_origin_frames}，无效 origin ${s.invalid_origin_frames}`);
 }
 return lines.join('\n');
}
