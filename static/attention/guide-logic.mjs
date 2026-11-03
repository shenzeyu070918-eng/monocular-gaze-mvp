// Operational timing only; frozen thresholds and block geometry live in Python.
export function phaseAt(seconds, actionSeconds=4) {
  if (!Number.isFinite(seconds) || seconds < 0 || !Number.isFinite(actionSeconds) || actionSeconds<4) throw new Error('invalid guide time');
  if (seconds < 16) return {kind:'target', id:Math.floor(seconds/2)+1};
  const phases=['scroll','tab','app','exclude','pause'];
  const index=Math.floor((seconds-16)/actionSeconds);
  return index>=phases.length ? {kind:'end'} : {kind:'phase',phase:phases[index]};
}
export function instruction(phase, app='备忘录', pauseKey='Control+Option+S') {
  return {scroll:'向下滚动到第 9 块',tab:'按 ⌘⇧] 切到右侧彩色标签',
    app:'按 ⌘Tab 切到 Finder 等普通应用',exclude:`切到「${app}」`,
    pause:`按 ${pauseKey} 暂停注意力会话`}[phase];
}
export function actionText(stage, app, pauseKey) {
  if(stage.feedback==='completed')return '✓ 完成';
  if(stage.feedback==='skipped')return '已跳过 · 记为未执行';
  if(stage.phase==='pause'&&stage.resume_ready)return `按 ${pauseKey} 恢复注意力会话`;
  return instruction(stage.phase,app,pauseKey);
}
export class BrowserEvidence {
  constructor(page){this.page=page;this.revision=0;}
  sample(reason,state){
    if(!['load','heartbeat','scroll','visibilitychange'].includes(reason))throw Error('invalid evidence source');
    if(reason==='scroll'||reason==='visibilitychange')this.revision++;
    return {page:this.page,revision:this.revision,reason,...state};
  }
}

export function clockEstimate(samples) {
  if (!samples.length) throw new Error('no clock samples');
  for (const s of samples) if (![s.before,s.after,s.serverNs].every(Number.isFinite)||s.after<s.before) throw new Error('invalid clock sample');
  const best=samples.reduce((a,b)=>a.after-a.before<=b.after-b.before?a:b);
  return {offsetNs:best.serverNs-(best.before+best.after)*500000,uncertaintyNs:(best.after-best.before)*500000};
}
