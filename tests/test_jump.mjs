import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {jumpAnchors,jumpLayout,generateJumpPoints,pointDistance} from '../static/jump.js';

function randomGenerator(seed){let current=seed;return ()=>{current=(current*1664525+1013904223)>>>0;return current/2**32;};}
const css=readFileSync(new URL('../static/style.css',import.meta.url),'utf8');
function style(selector){return css.split(`${selector}{`)[1].split('}')[0];}
function size(selector){const rule=style(selector);return ['width','height'].map(key=>parseFloat(rule.match(new RegExp(`(?:^|;)${key}:([0-9.]+)px`))[1]));}
const target=size('#jumpTarget'),hint=size('#jumpEscape');
assert.match(style('#jumpTarget'),/box-sizing:border-box/);
assert.match(style('#jumpTarget'),/transform:translate\(-50%,-50%\)/);
assert.match(style('#jumpOverlay'),/position:fixed;inset:0/);
assert.match(style('#jumpEscape'),/box-sizing:border-box/);
assert.match(style('#jumpEscape'),/(?:^|;)left:0;/);
assert.match(style('#jumpEscape'),/(?:^|;)top:0;/);
assert.match(style('#jumpEscape'),/overflow:hidden/);
assert.equal(css.includes('.jump-banner'),false);
assert.match(style('html.jump-active'),/overflow:hidden/);
const html=readFileSync(new URL('../static/index.html',import.meta.url),'utf8');
assert.equal(html.includes('jump-banner'),false);
assert.equal(html.includes('jumpCancel'),false);
assert.match(html,/id="jumpEscape" hidden>Esc 取消/);

for(const margin of [.02,.05,.18,.3]){
 const anchors=[[margin,margin],[1-margin,margin],[1-margin,1-margin],[margin,1-margin],
                [.5,margin],[1-margin,.5],[.5,1-margin],[margin,.5]];
 assert.deepEqual(jumpAnchors(margin),anchors);
 for(let seed=1;seed<=30;seed++){
  const points=generateJumpPoints({point_count:25,dwell_ms:1200,min_distance:.35,margin},randomGenerator(seed));
  assert.equal(points.length,25);
  for(const anchor of anchors)assert.ok(points.some(p=>p[0]===anchor[0]&&p[1]===anchor[1]));
  points.forEach(p=>p.forEach(v=>assert.ok(v>=margin&&v<=1-margin)));
  for(let i=1;i<points.length;i++)assert.ok(pointDistance(points[i-1],points[i])>=.35);
  for(const viewport of [[1920,1080],[1000,800],[640,480],[320,240],[3840,2160]]){
   const layoutArgs={margin,viewport,target,hint};
   if(viewport.some((v,i)=>margin*v<target[i]/2)){
    assert.throws(()=>jumpLayout(layoutArgs),/完整显示目标圆/);
    continue;
   }
   const layout=jumpLayout(layoutArgs);
   for(const p of points){
    const [x,y]=p.map((v,i)=>v*viewport[i]),left=x-target[0]/2,top=y-target[1]/2;
    assert.ok(left>=0&&top>=0);
    assert.ok(x+target[0]/2<=viewport[0]&&y+target[1]/2<=viewport[1]);
    if(layout.hintVisible)assert.ok(left>=hint[0]||top>=hint[1]);
   }
   // Guard the entire continuous allowed region, including ungenerated positions.
   if(layout.hintVisible)assert.ok(hint[0]<=margin*viewport[0]-target[0]/2||hint[1]<=margin*viewport[1]-target[1]/2);
  }
 }
}
const options={point_count:8,dwell_ms:1,min_distance:.1,margin:.05};
assert.throws(()=>generateJumpPoints({...options,point_count:7}),/至少为 8/);
assert.throws(()=>generateJumpPoints({...options,dwell_ms:0}),/停留时间/);
for(const min_distance of [0,2])assert.throws(()=>generateJumpPoints({...options,min_distance}),/最小间距/);
assert.throws(()=>generateJumpPoints({...options,margin:.4,min_distance:.3}),/边距内区域/);
assert.throws(()=>generateJumpPoints({...options,min_distance:1.2}),/无法同时覆盖/);
for(const margin of [undefined,NaN,0,-.1,.5,1]){
 assert.throws(()=>generateJumpPoints({...options,margin}),/屏幕边距/);
 assert.throws(()=>jumpLayout({margin,viewport:[1000,800],target,hint}),/屏幕边距/);
}
assert.deepEqual(jumpLayout({margin:.05,viewport:[1920,1080],target,hint}),{hintVisible:true});
assert.deepEqual(jumpLayout({margin:.05,viewport:[640,480],target,hint}),{hintVisible:false});
assert.throws(()=>jumpLayout({margin:.05,viewport:[320,240],target,hint}),/完整显示目标圆/);
assert.throws(()=>jumpLayout({margin:.05,viewport:[0,800],target,hint}),/全屏尺寸/);
assert.equal(pointDistance([0,0],[1,1]),Math.SQRT2);
console.log('jump inset bounds, complete circles, non-overlapping hint and minimum-distance tests passed');
