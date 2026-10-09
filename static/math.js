export const affine = (p,m) => [0,1].map(j=>p[0]*m[0][j]+p[1]*m[1][j]+m[2][j]);
export const percentile = (a,q) => {if(!a.length)return null;const b=[...a].sort((x,y)=>x-y);const k=(b.length-1)*q;return b[Math.floor(k)]+(b[Math.ceil(k)]-b[Math.floor(k)])*(k%1);};
export function summary(values){const a=values.filter(Number.isFinite);return a.length?{mean:a.reduce((x,y)=>x+y,0)/a.length,median:percentile(a,.5),p90:percentile(a,.9),within15:a.filter(x=>x<=1.5).length/a.length,within2:a.filter(x=>x<=2).length/a.length}:null;}
export const errorDistance=(p,t,w,h)=>Math.hypot((p[0]-t[0])*w,(p[1]-t[1])*h);
