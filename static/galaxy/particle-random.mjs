// Shared deterministic source. Moving this preserves planet/text seed sequences.
export function randomSource(seed){let s=seed>>>0;return()=>{s=(Math.imul(s,1664525)+1013904223)>>>0;return s/4294967296;};}
