"""Block candidates and duration conserving hard/Gaussian soft assignment."""
import math
import cv2
import numpy as np
from gaze_core.devices import finite


def validate_regions(rows):
    result=[];seen=set()
    for item in rows:
        name=item['id'];rect=[finite(v,'AOI.rect') for v in item['rect']]
        if not isinstance(name,str) or not name or name in seen or len(rect)!=4:raise ValueError('AOI需要唯一id及四维矩形')
        a,b,c,d=rect
        if not 0<=a<c<=1 or not 0<=b<d<=1:raise ValueError('AOI矩形必须在[0,1]内')
        seen.add(name);result.append(dict(item,rect=rect))
    return result


def blocks(lines,pixels,scale,options,manual=()):
    regions=[]
    for line in sorted(lines,key=lambda r:(r['rect'][1],r['rect'][0])):
        rect=list(line['rect']);a,b,c,d=rect;target=None
        for r in regions:
            x,y,z,w=r['rect'];overlap=max(0,min(c,z)-max(a,x))/max(1e-9,min(c-a,z-x))
            if overlap>.5 and 0<=b-w<=scale['sigma_y']*options.aoi_merge_gap_sigma:target=r;break
        if target is None:regions.append(dict(id=f'text-{len(regions)+1}',rect=rect,kind='text_block_candidate',text=line['text'],source='ocr',confidence=line['confidence']))
        else:
            x,y,z,w=target['rect'];target['rect']=[min(a,x),min(b,y),max(c,z),max(d,w)];target['text']+='\n'+line['text']
            target['confidence']=min(target['confidence'],line['confidence'])
    gray=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY);small=cv2.resize(gray,(options.thumbnail_width,max(1,round(gray.shape[0]*options.thumbnail_width/gray.shape[1]))))
    edges=cv2.Canny(small,40,120);edges=cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    contours,_=cv2.findContours(edges,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour);rect=[x/small.shape[1],y/small.shape[0],(x+w)/small.shape[1],(y+h)/small.shape[0]]
        if w*h/small.size<.015:continue
        if any(max(rect[0],a)<min(rect[2],c) and max(rect[1],b)<min(rect[3],d) for a,b,c,d in [r['rect'] for r in regions]):continue
        regions.append(dict(id=f'visual-{len(regions)+1}',rect=rect,kind='image_or_color_candidate',source='edges',confidence=None))
    # Also capture large, nearly uniform color components, not just textures.
    color=cv2.resize(pixels,(small.shape[1],small.shape[0]));quantized=(color//32).astype(np.int32);codes=quantized[:,:,0]*64+quantized[:,:,1]*8+quantized[:,:,2]
    for code in np.unique(codes):
        count,labels,stats,_=cv2.connectedComponentsWithStats((codes==code).astype(np.uint8),8)
        for x,y,w,h,area in stats[1:]:
            if area/small.size<.03 or area/small.size>.9:continue
            rect=[x/small.shape[1],y/small.shape[0],(x+w)/small.shape[1],(y+h)/small.shape[0]]
            if any(max(rect[0],a)<min(rect[2],c) and max(rect[1],b)<min(rect[3],d) for a,b,c,d in [r['rect'] for r in regions]):continue
            regions.append(dict(id=f'color-{len(regions)+1}',rect=rect,kind='color_candidate',source='color_component',confidence=None))
    # Merge adjacent sub-error-scale blocks. Do not join distant unrelated content.
    for r in list(regions):
        a,b,c,d=r['rect']
        if c-a>=scale['sigma_x'] and d-b>=scale['sigma_y']:continue
        others=[v for v in regions if v is not r]
        if not others:continue
        def gap(v):
            x,y,z,w=v['rect'];return math.hypot(max(x-c,a-z,0)/scale['sigma_x'],max(y-d,b-w,0)/scale['sigma_y'])
        target=min(others,key=gap)
        if gap(target)<=options.aoi_merge_gap_sigma:
            x,y,z,w=target['rect'];target['rect']=[min(a,x),min(b,y),max(c,z),max(d,w)]
            target.setdefault('merged_ids',[]).append(r['id']);regions.remove(r)
        else:r['below_error_scale']=True
    manual=validate_regions(manual)
    names={r['id'] for r in manual};regions=[r for r in regions if r['id'] not in names]
    return validate_regions([dict(r,source='manual') for r in manual]+regions)


class AOIStatistics:
    def __init__(self,regions,scale,grid_width=320,aspect=1.):
        self.regions=validate_regions(regions);self.scale=scale;self.total=0.;self.last=None
        self.rows={r['id']:dict(id=r['id'],hard_s=0.,soft_s=0.,first_fixation_ns=None,entries=0,fixations=0) for r in regions}
        self.background_hard=0.;self.background_soft=0.;self.outside_soft=0.
        h=max(1,round(grid_width/aspect));self.x=np.linspace(0,1,grid_width+1);self.y=np.linspace(0,1,h+1)
        self.labels=np.zeros((h,grid_width),int)
        # Manual first, then smallest blocks: deterministic non-overlapping partition.
        self.ordered=sorted(regions,key=lambda r:(r.get('source')!='manual',(r['rect'][2]-r['rect'][0])*(r['rect'][3]-r['rect'][1]),r['id']))
        xx,yy=np.meshgrid((self.x[:-1]+self.x[1:])/2,(self.y[:-1]+self.y[1:])/2)
        for n,r in enumerate(self.ordered,1):
            a,b,c,d=r['rect'];mask=(xx>=a)&(xx<c)&(yy>=b)&(yy<d)&(self.labels==0);self.labels[mask]=n

    def add(self,segment):
        u,v=segment['center'];duration=(segment['end_ns']-segment['start_ns'])/1e9
        if duration<=0:return
        self.total+=duration;chosen=None
        for r in self.ordered:
            a,b,c,d=r['rect']
            if a<=u<=c and b<=v<=d:chosen=r['id'];break
        if chosen is None:self.background_hard+=duration
        else:
            row=self.rows[chosen];row['hard_s']+=duration;row['fixations']+=1
            if row['first_fixation_ns'] is None:row['first_fixation_ns']=segment['start_ns']
            if chosen!=self.last:row['entries']+=1
        self.last=chosen
        def masses(edges,center,sigma):
            cdf=np.array([.5*(1+math.erf((x-center)/(math.sqrt(2)*sigma))) for x in edges]);return np.diff(cdf)
        weights=np.outer(masses(self.y,v,self.scale['sigma_y']),masses(self.x,u,self.scale['sigma_x']))
        totals=np.bincount(self.labels.ravel(),weights=weights.ravel(),minlength=len(self.ordered)+1)
        self.background_soft+=duration*totals[0];self.outside_soft+=duration*max(0.,1-float(weights.sum()))
        for n,r in enumerate(self.ordered,1):self.rows[r['id']]['soft_s']+=duration*totals[n]

    def snapshot(self):
        rows=[dict(r,hard_fraction=r['hard_s']/self.total if self.total else None,soft_fraction=r['soft_s']/self.total if self.total else None) for r in self.rows.values()]
        return dict(valid_fixation_s=self.total,regions=rows,background_hard_s=self.background_hard,background_soft_s=self.background_soft,outside_probability_s=self.outside_soft,
                    soft_definition='Gaussian cell integrals; disjoint AOI ownership; background and offscreen probability retained')
