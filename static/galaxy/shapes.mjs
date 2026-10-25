// Procedural geometry: no downloaded images/fonts and no screen contents.
export function makeShapes(THREE,count,text,seed=918) {
  let state=seed;
  const random=()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return state/4294967296;};
  const arrays=Array.from({length:4},()=>new Float32Array(count*4));
  // Coarse hand-authored continental silhouettes (longitude/latitude), not a map asset.
  const continents=[
    [[-17,37],[10,37],[33,31],[43,12],[51,10],[42,-12],[35,-35],[18,-35],[10,-10],[-10,5],[-17,25],[-17,37]],
    [[-10,36],[-10,55],[10,71],[35,70],[60,77],[100,75],[145,60],[180,65],[170,45],[140,35],[125,5],[105,-6],[100,20],[80,8],[65,25],[45,12],[35,35],[-10,36]],
    [[-168,70],[-140,70],[-125,55],[-130,45],[-117,30],[-100,15],[-83,8],[-80,24],[-60,48],[-80,60],[-100,75],[-168,70]],
    [[-81,12],[-60,5],[-35,-6],[-42,-23],[-60,-55],[-75,-45],[-80,-15],[-81,12]],
    [[112,-22],[130,-12],[152,-23],[145,-39],[116,-35],[112,-22]],
    [[-52,60],[-20,70],[-30,83],[-60,80],[-52,60]]
  ];
  const coasts=continents.flatMap(path=>path.slice(1).map((p,i)=>[path[i],p]));
  // The browser rasterizes ONLY configurable public Demo text, never a screen.
  const canvas=document.createElement('canvas');canvas.width=1024;canvas.height=256;
  const ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.fillStyle='black';ctx.fillRect(0,0,1024,256);
  ctx.font='bold 180px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='white';
  ctx.fillText(text,512,128,960);
  const pixels=ctx.getImageData(0,0,1024,256).data,letters=[];
  for(let y=0;y<256;y+=2)for(let x=0;x<1024;x+=2)if(pixels[(y*1024+x)*4]>100)letters.push([x,y]);
  for(let i=0;i<count;i++) {
    const radius=Math.sqrt(random())*2.7,arm=i%4,theta=radius*2.4+arm*Math.PI/2+(random()-.5)*.5;
    arrays[0].set([Math.cos(theta)*radius,Math.sin(theta)*radius,(random()-.5)*.16,1],i*4);
    const t=random()*Math.PI*2;
    arrays[1].set([16*Math.sin(t)**3/7,(13*Math.cos(t)-5*Math.cos(2*t)-2*Math.cos(3*t)-Math.cos(4*t))/7,(random()-.5)*.3,1],i*4);
    const p=letters.length?letters[Math.floor(random()*letters.length)]:[512,128];
    arrays[2].set([(p[0]-512)/150,(128-p[1])/150,(random()-.5)*.2,1],i*4);
    // Globe: sphere, latitude/longitude bands and coarse continental outlines.
    const a=random()*Math.PI*2,b=Math.acos(2*random()-1),band=i%3;
    const lat=band===0?Math.round(b*8/Math.PI)*Math.PI/8:b;
    const lon=band===1?Math.round(a*12/(2*Math.PI))*2*Math.PI/12:a;
    if(band===2){
      const [from,to]=coasts[Math.floor(random()*coasts.length)],f=random();
      const latitude=(from[1]+(to[1]-from[1])*f)*Math.PI/180,longitude=(from[0]+(to[0]-from[0])*f)*Math.PI/180;
      arrays[3].set([2.12*Math.cos(latitude)*Math.sin(longitude),2.12*Math.sin(latitude),2.12*Math.cos(latitude)*Math.cos(longitude),1],i*4);
    }else arrays[3].set([2.1*Math.sin(lat)*Math.cos(lon),2.1*Math.cos(lat),2.1*Math.sin(lat)*Math.sin(lon),1],i*4);
  }
  return arrays;
}
