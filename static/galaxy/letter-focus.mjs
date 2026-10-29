import {Spring2D} from './logic.mjs';
export const MAX_LETTERS=80;
export function letterLayout(mask,width,height,scale,x0,y0,ranges=[[0,width]]){
 const columns=new Uint8Array(width),letters=ranges.map((_,id)=>({id,bounds:[Infinity,-Infinity,Infinity,-Infinity]}));
 for(let x=0,index=0;x<width;x++){while(index<ranges.length-1&&x>=ranges[index][1])index++;columns[x]=index;}
 for(let y=0;y<height;y++)for(let x=0;x<width;x++)if(mask[y*width+x]){
  const b=letters[columns[x]].bounds,px=x0+x*scale,py=y0-y*scale;
  b[0]=Math.min(b[0],px-scale/2);b[1]=Math.max(b[1],px+scale/2);b[2]=Math.min(b[2],py-scale/2);b[3]=Math.max(b[3],py+scale/2);
 }
 for(const l of letters)l.center=Number.isFinite(l.bounds[0])?[(l.bounds[0]+l.bounds[1])/2,(l.bounds[2]+l.bounds[3])/2]:[0,0];
 return {columns,letters:letters.filter(l=>Number.isFinite(l.bounds[0]))};
}
export function letterHit(letters,point,weights,scaleGain=0){
 if(!point)return -1;let result=-1,best=Infinity;
 for(const l of letters){const scale=1+scaleGain*(weights?.[l.id]??0),b=l.bounds,c=l.center;
  if(point[0]<c[0]+(b[0]-c[0])*scale||point[0]>c[0]+(b[1]-c[0])*scale||point[1]<c[1]+(b[2]-c[1])*scale||point[1]>c[1]+(b[3]-c[1])*scale)continue;
  const distance=(point[0]-c[0])**2+(point[1]-c[1])**2;if(distance<best){best=distance;result=l.id;}}
 return result;
}
export class LetterFocus{
 constructor(letters,hz){this.letters=letters;this.hz=hz;this.reset();}
 reset(){this.weights??=new Float32Array(MAX_LETTERS);this.weights.fill(0);this.springs=this.letters.map(()=>new Spring2D());this.selected=-1;}
 step(id,dt){this.selected=id;this.letters.forEach((l,n)=>{this.weights[l.id]=Math.max(0,Math.min(1,this.springs[n].step([Number(l.id===id),0],dt,this.hz)[0]));});return this.weights;}
}
