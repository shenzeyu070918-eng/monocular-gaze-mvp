export const JUMP_ANCHORS=Object.freeze([[0,0],[1,0],[1,1],[0,1],[.5,0],[1,.5],[.5,1],[0,.5]].map(Object.freeze));
export const pointDistance=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
function shuffle(values,random){const result=[...values];for(let i=result.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[result[i],result[j]]=[result[j],result[i]];}return result;}
export function generateJumpPoints({point_count,dwell_ms,min_distance},random=Math.random){
 if(!Number.isInteger(point_count)||point_count<JUMP_ANCHORS.length)throw Error('点数至少为 8，才能覆盖四角及四条边。');
 if(!Number.isFinite(dwell_ms)||dwell_ms<=0)throw Error('停留时间必须大于 0。');
 if(!Number.isFinite(min_distance)||min_distance<=0||min_distance>Math.SQRT2)throw Error('最小间距需大于 0，且不能超过全屏对角线。');
 const anchors=shuffle(JUMP_ANCHORS,random);
 function search(path,remaining){if(!remaining.length)return path;for(let i=0;i<remaining.length;i++){const p=remaining[i];if(!path.length||pointDistance(path.at(-1),p)>=min_distance){const result=search([...path,p],remaining.filter((_,j)=>i!==j));if(result)return result;}}return null;}
 const result=search([],anchors);
 if(!result)throw Error('当前最小间距无法同时覆盖四角和四条边，请减小间距。');
 while(result.length<point_count){let p=null;for(let i=0;i<2000;i++){const candidate=[random(),random()];if(pointDistance(result.at(-1),candidate)>=min_distance){p=candidate;break;}}if(!p)p=shuffle(JUMP_ANCHORS,random).find(candidate=>pointDistance(result.at(-1),candidate)>=min_distance);if(!p)throw Error('当前点数与最小间距无法生成跳点序列。');result.push([...p]);}
 return result.map(p=>[...p]);
}
