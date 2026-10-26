import {cornerVisual} from './scene-controls.mjs';
export class CornerIndicator{
 constructor(options){
  this.root=document.createElement('div');this.root.id='corner-progress';this.root.setAttribute('aria-hidden','true');
  this.root.style.setProperty('--corner-size',options.corner_feedback_px+'px');
  this.root.style.setProperty('--corner-margin',options.corner_feedback_margin_px+'px');
  this.root.style.setProperty('--corner-fade',options.corner_feedback_fade_ms+'ms');
  this.edges=options.scene_switch_mode==='edges';
  this.root.innerHTML=this.edges?[0,1].map(i=>`<div class="edge-meter edge-${i}"><div class="edge-fill"></div></div>`).join(''):[0,1,2,3].map(i=>`<svg class="corner-meter corner-${i}" viewBox="0 0 44 44"><circle class="corner-track" cx="22" cy="22" r="18"/><circle class="corner-arc" cx="22" cy="22" r="18" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1" transform="rotate(-90 22 22)"/></svg>`).join('');
  document.body.append(this.root);this.meters=[...this.root.children];this.arcs=this.meters.map(m=>m.querySelector(this.edges?'.edge-fill':'.corner-arc'));this.last='';
 }
 draw(status,usable=true){
  const visual=cornerVisual(status,usable),key=JSON.stringify(visual);if(key===this.last)return;this.last=key;
  this.meters.forEach((meter,i)=>{meter.style.opacity=String(i===visual.corner?visual.opacity:0);if(i===visual.corner){if(this.edges)this.arcs[i].style.transform='scaleY('+visual.progress+')';else this.arcs[i].setAttribute('stroke-dashoffset',String(1-visual.progress));}});
 }
 dispose(){this.root.remove();}
}
