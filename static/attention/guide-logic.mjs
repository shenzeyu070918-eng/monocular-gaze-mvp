// Operational timing only; frozen thresholds and block geometry live in Python.
export function phaseAt(seconds, actionSeconds=4) {
  if (!Number.isFinite(seconds) || seconds < 0 || !Number.isFinite(actionSeconds) || actionSeconds<4) throw new Error('invalid guide time');
  if (seconds < 16) return {kind:'target', id:Math.floor(seconds/2)+1};
  const phases=['scroll','tab','app','exclude','pause'];
  const index=Math.floor((seconds-16)/actionSeconds);
  return index>=phases.length ? {kind:'end'} : {kind:'phase',phase:phases[index]};
}
export function instruction(phase, app='备忘录', pauseKey='Control+Option+S') {
  return {scroll:'向下滚过至少半屏，看到编号 9–16 后停住至少1.5秒',
    tab:'按 ⌘⇧] 切到右边彩色测试标签，停住至少1.5秒，再按 ⌘⇧[ 返回',
    app:`按 ⌘Tab 切到 Finder 等普通应用（不要选${app}），停住至少1.5秒，再返回`,
    exclude:`切到「${app}」，停住至少1.5秒，再返回测试页`,
    pause:`按 ${pauseKey} 暂停，等1秒，再按一次恢复`}[phase];
}
export function clockEstimate(samples) {
  if (!samples.length) throw new Error('no clock samples');
  for (const s of samples) if (![s.before,s.after,s.serverNs].every(Number.isFinite)||s.after<s.before) throw new Error('invalid clock sample');
  const best=samples.reduce((a,b)=>a.after-a.before<=b.after-b.before?a:b);
  return {offsetNs:best.serverNs-(best.before+best.after)*500000,uncertaintyNs:(best.after-best.before)*500000};
}
