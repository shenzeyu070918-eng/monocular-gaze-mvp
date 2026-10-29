// Known-layout acceptance timing only. All screen/client mapping is in Python.
export function phaseAt(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) throw new Error('invalid guide time');
  if (seconds < 16) return {kind: 'target', id: Math.floor(seconds / 2) + 1};
  if (seconds >= 30) return {kind: 'end'};
  const phases = [[16, 'scroll'], [18, 'tab'], [20, 'app'], [22, 'exclude'], [26, 'pause']];
  return {kind: 'phase', phase: phases.findLast(([start]) => seconds >= start)[1]};
}
export function clockEstimate(samples) {
  if (!samples.length) throw new Error('no clock samples');
  for (const s of samples) if (![s.before, s.after, s.serverNs].every(Number.isFinite) || s.after < s.before) throw new Error('invalid clock sample');
  const best = samples.reduce((a, b) => a.after - a.before <= b.after - b.before ? a : b);
  return {offsetNs: best.serverNs - (best.before + best.after) * 500000, uncertaintyNs: (best.after - best.before) * 500000};
}
