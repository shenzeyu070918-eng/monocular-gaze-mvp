const UNIT_ANCHORS=Object.freeze([[0,0],[1,0],[1,1],[0,1],[.5,0],[1,.5],[.5,1],[0,.5]].map(Object.freeze));
export const pointDistance=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
function validateMargin(margin){if(!Number.isFinite(margin)||margin<=0||margin>=.5)throw Error('屏幕边距必须大于 0% 且小于 50%。');}
export function jumpAnchors(margin){validateMargin(margin);return UNIT_ANCHORS.map(p=>p.map(v=>v===0?margin:v===1?1-margin:.5));}
export function jumpLayout({margin,viewport,target,hint}){
 validateMargin(margin);
 for(const size of [viewport,target,hint])if(!Array.isArray(size)||size.length!==2||size.some(v=>!Number.isFinite(v)||v<=0))throw Error('无法确定全屏尺寸或目标尺寸，请重新进入全屏。');
 if(viewport.some((v,i)=>margin*v<target[i]/2))throw Error('当前边距无法完整显示目标圆，请增大屏幕边距或全屏尺寸。');
 // The top-left hint stays wholly outside the bounding region of every circle.
 return {hintVisible:hint.some((v,i)=>v<=margin*viewport[i]-target[i]/2)};
}
function shuffle(values,random){const result=[...values];for(let i=result.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[result[i],result[j]]=[result[j],result[i]];}return result;}
export function generateJumpPoints({point_count,dwell_ms,min_distance,margin},random=Math.random){
 validateMargin(margin);
 if(!Number.isInteger(point_count)||point_count<UNIT_ANCHORS.length)throw Error('点数至少为 8，才能覆盖四角及四条边。');
 if(!Number.isFinite(dwell_ms)||dwell_ms<=0)throw Error('停留时间必须大于 0。');
 if(!Number.isFinite(min_distance)||min_distance<=0||min_distance>Math.SQRT2*(1-2*margin))throw Error('最小间距需大于 0，且不能超过边距内区域的对角线。');
 const anchors=shuffle(jumpAnchors(margin),random);
 function search(path,remaining){if(!remaining.length)return path;for(let i=0;i<remaining.length;i++){const p=remaining[i];if(!path.length||pointDistance(path.at(-1),p)>=min_distance){const result=search([...path,p],remaining.filter((_,j)=>i!==j));if(result)return result;}}return null;}
 const result=search([],anchors);
 if(!result)throw Error('当前最小间距无法同时覆盖四角和四条边，请减小间距。');
 while(result.length<point_count){let p=null;for(let i=0;i<2000;i++){const candidate=[0,1].map(()=>Math.min(1-margin,margin+(1-2*margin)*random()));if(pointDistance(result.at(-1),candidate)>=min_distance){p=candidate;break;}}if(!p)p=shuffle(anchors,random).find(candidate=>pointDistance(result.at(-1),candidate)>=min_distance);if(!p)throw Error('当前点数与最小间距无法生成跳点序列。');result.push([...p]);}
 return result.map(p=>[...p]);
}
