// Shared by the standalone editor and Node tests; no I/O or DOM dependencies.
export const clamp = v => Math.max(0, Math.min(1, v));
export function rectFromPoints(a,b) { return [Math.min(clamp(a[0]),clamp(b[0])),Math.min(clamp(a[1]),clamp(b[1])),Math.max(clamp(a[0]),clamp(b[0])),Math.max(clamp(a[1]),clamp(b[1]))]; }
export function hitRegion(rows,p) { for(let i=rows.length-1;i>=0;i--) { const [a,b,c,d]=rows[i].rect; if(a<=p[0]&&p[0]<=c&&b<=p[1]&&p[1]<=d)return i; } return -1; }
export function moveRect(rect,delta) { const [a,b,c,d]=rect,dx=Math.max(-a,Math.min(1-c,delta[0])),dy=Math.max(-b,Math.min(1-d,delta[1]));return [a+dx,b+dy,c+dx,d+dy]; }
export function resizeRect(rect,corner,p) { const opposite=[[rect[2],rect[3]],[rect[0],rect[3]],[rect[0],rect[1]],[rect[2],rect[1]]][corner];return rectFromPoints(opposite,p); }
export function fitImage(width,height,maxWidth,maxHeight) {
 const scale=Math.min(maxWidth/width,maxHeight/height);
 return [width*scale,height*scale];
}
export function automaticFramesFillImage(rows) {
 const boxes=rows.filter(r=>r.automatic).map(r=>r.rect);
 const edges=[...new Set([0,1,...boxes.flatMap(([a,,c])=>[a,c])])].sort((a,b)=>a-b);
 return edges.slice(1).every((right,i)=>{
  const left=edges[i];
  const ranges=boxes.filter(([a,,c])=>a<=left&&right<=c).map(([,b,,d])=>[b,d]).sort((a,b)=>a[0]-b[0]);
  let covered=0;
  for(const [top,bottom] of ranges){
   if(top>covered)return false;
   covered=Math.max(covered,bottom);
  }
  return covered>=1;
 });
}
export function cleanRegions(rows) {
 if(!Array.isArray(rows)||rows.length>1000)throw Error('区域必须为矩形列表'); const ids=new Set();
 return rows.map(r=>{if(typeof r.id!=='string'||!r.id||r.id.length>80||ids.has(r.id))throw Error('区域编号重复或无效');ids.add(r.id);
 const rect=r.rect;if(!Array.isArray(rect)||rect.length!==4||rect.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0||v>1)||!(rect[0]<rect[2]&&rect[1]<rect[3]))throw Error('区域坐标无效');
 const name=String(r.name??'').trim();if(name.length>80)throw Error('名称最多80字');return {id:r.id,name,rect:[...rect]};});
}
export function makeDocument(scenes,meta) {
 const common={schema_version:2,coordinate_space:meta.mode==='stimulus'?'image_normalized':'full_screen_normalized'};
 if(meta.mode==='stimulus')return {...common,stimulus_sha256:meta.stimulus_sha256,regions:cleanRegions(scenes[0].regions)};
 return {...common,scenes:Object.fromEntries(scenes.map(s=>[s.id,cleanRegions(s.regions)]))};
}
export function importDocument(document,scenes,meta) {
 if(document.schema_version!==2)throw Error('请选择版本2手动AOI JSON');
 const expected=meta.mode==='stimulus'?'image_normalized':'full_screen_normalized';if(document.coordinate_space!==expected)throw Error('坐标空间不一致');
 if(meta.mode==='stimulus'&&document.stimulus_sha256!==meta.stimulus_sha256)throw Error('图片SHA256不同');
 return scenes.map(s=>({...s,regions:cleanRegions(meta.mode==='stimulus'?document.regions:(document.scenes?.[s.id]??s.regions))}));
}
