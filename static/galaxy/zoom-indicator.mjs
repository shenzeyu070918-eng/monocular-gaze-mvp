// One small SVG overlay; no full-screen canvas or extra WebGL pass.
export class ZoomIndicator{
 constructor(options){
  this.root=document.createElementNS('http://www.w3.org/2000/svg','svg');this.root.id='zoom-progress';this.root.setAttribute('viewBox','0 0 100 100');
  this.root.innerHTML='<circle cx="50" cy="50" r="48" pathLength="1" stroke-dasharray="1" stroke-dashoffset="1" transform="rotate(-90 50 50)"/>';
  this.root.style.transition='opacity '+options.zoom_feedback_fade_ms+'ms ease-out';this.arc=this.root.firstElementChild;document.body.append(this.root);this.last='';
 }
 draw(f){
  const visible=f?.visible&&f.progress>0&&(!f.active||f.zoom<.95);
  const key=visible?[...f.center,f.radius,f.progress].join(','):'hidden';if(key===this.last)return;this.last=key;
  this.root.style.opacity=visible?'.65':'0';if(!visible)return;
  this.root.style.left=f.center[0]+'px';this.root.style.top=f.center[1]+'px';this.root.style.width=this.root.style.height=2*f.radius+'px';
  this.arc.setAttribute('stroke-dashoffset',String(1-f.progress));
 }
 dispose(){this.root.remove();}
}
