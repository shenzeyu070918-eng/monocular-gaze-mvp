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


AOI_ALGORITHM_VERSION = 'hierarchical-v2'


def primary_regions(rows):
    """Legacy flat AOIs are primary; children never double-count parent time."""
    return [r for r in rows if r.get('role','primary')=='primary' and r.get('ranked',True)]


def area(rect):return max(0.,rect[2]-rect[0])*max(0.,rect[3]-rect[1])

def intersection(a,b):return max(0.,min(a[2],b[2])-max(a[0],b[0]))*max(0.,min(a[3],b[3])-max(a[1],b[1]))


def visual_containers(pixels,options):
    """Rectangles first, at enough resolution to keep thin card borders.

    RETR_LIST retains cards inside a page outline; RETR_EXTERNAL lost them.
    Nested duplicate edges are suppressed before deciding visual hierarchy.
    """
    height,width=pixels.shape[:2];w=min(width,options.aoi_detection_width);h=max(1,round(height*w/width))
    color=cv2.resize(pixels,(w,h));gray=cv2.cvtColor(color,cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,options.aoi_edge_low,options.aoi_edge_high)
    edges=cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((options.aoi_close_px,options.aoi_close_px),np.uint8))
    contours,_=cv2.findContours(edges,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE);candidates=[]
    def candidate(x,y,bw,bh,source,fill):
        fraction=bw*bh/(w*h)
        if not options.aoi_container_min_area<=fraction<=options.aoi_container_max_area:return
        if max(bw/bh,bh/bw)>options.aoi_container_max_aspect or fill<options.aoi_container_fill:return
        candidates.append(dict(rect=[x/w,y/h,(x+bw)/w,(y+bh)/h],source=source,kind='visual_container'))
    for contour in contours:
        x,y,bw,bh=cv2.boundingRect(contour)
        candidate(x,y,bw,bh,'opencv_border',cv2.contourArea(contour)/max(1,bw*bh))
    quantized=color//options.aoi_color_bin;bins=math.ceil(256/options.aoi_color_bin)
    codes=quantized[:,:,0].astype(np.int32)*bins*bins+quantized[:,:,1].astype(np.int32)*bins+quantized[:,:,2]
    values,counts=np.unique(codes,return_counts=True)
    for code in values[np.argsort(counts)[-options.aoi_max_colors:]]:
        _,_,components,_=cv2.connectedComponentsWithStats((codes==code).astype(np.uint8),8)
        for x,y,bw,bh,count in components[1:]:candidate(x,y,bw,bh,'opencv_color',count/max(1,bw*bh))
    distinct=[]
    for r in sorted(candidates,key=lambda v:(v['source']!='opencv_border',-area(v['rect']))):
        if any(intersection(r['rect'],v['rect'])/max(1e-12,area(r['rect'])+area(v['rect'])-intersection(r['rect'],v['rect']))>=options.aoi_container_iou for v in distinct):continue
        distinct.append(r)
    # A full-width page/layout wrapper is not a card. Keep its individual cards.
    kept=[]
    for r in distinct:
        children=[v for v in distinct if v is not r and area(v['rect'])<area(r['rect'])*options.aoi_nested_ratio and intersection(r['rect'],v['rect'])>=area(v['rect'])*options.aoi_containment]
        if r['rect'][2]-r['rect'][0]>=options.aoi_wrapper_width and len(children)>=2:continue
        kept.append(r)
    mains=[]
    for r in kept:
        if any(v is not r and area(v['rect'])>area(r['rect'])/options.aoi_nested_ratio and intersection(r['rect'],v['rect'])>=area(r['rect'])*options.aoi_containment for v in kept):continue
        mains.append(r)
    return sorted(mains,key=lambda r:(round(r['rect'][1]/options.aoi_reading_row_tolerance),r['rect'][0]))


def clean_ocr(lines,options):
    import unicodedata
    result=[]
    for row in lines:
        text=unicodedata.normalize('NFKC',str(row.get('text',''))).strip()
        if sum(c.isalnum() for c in text)<options.aoi_ocr_min_chars:continue
        try:rect=validate_regions([dict(id='line',rect=row['rect'])])[0]['rect'];confidence=float(row.get('confidence',0.))
        except (ValueError,TypeError,KeyError):continue
        if area(rect)<options.aoi_ocr_min_area or not math.isfinite(confidence) or confidence<options.aoi_ocr_min_confidence:continue
        result.append(dict(row,text=text,rect=rect,confidence=confidence))
    return sorted(result,key=lambda r:(r['rect'][1],r['rect'][0]))


def text_blocks(lines,scale,options):
    regions=[]
    for line in lines:
        a,b,c,d=line['rect'];target=None
        for r in reversed(regions):
            x,y,z,w=r['rect'];overlap=max(0,min(c,z)-max(a,x));narrow=min(c-a,z-x);wide=max(c-a,z-x)
            # Both relative overlap and union-width guard are column-aware;
            # a wide heading cannot glue the next left/right columns together.
            if (overlap/max(1e-9,wide)>=options.aoi_line_overlap and (max(c,z)-min(a,x))<=wide*options.aoi_column_width_ratio
                    and wide<=narrow*options.aoi_column_width_ratio and 0<=b-w<=scale['sigma_y']*options.aoi_merge_gap_sigma):target=r;break
        if target is None:regions.append(dict(id='text-'+str(len(regions)+1),rect=list(line['rect']),kind='text_block_candidate',text=line['text'],source='ocr',confidence=line['confidence']))
        else:target['rect']=[min(a,x),min(b,y),max(c,z),max(d,w)];target['text']+='\n'+line['text'];target['confidence']=min(target['confidence'],line['confidence'])
    for r in regions:
        if '\n' in r['text']:r['kind']='paragraph_candidate'
        if r['rect'][2]-r['rect'][0]<scale['sigma_x'] or r['rect'][3]-r['rect'][1]<scale['sigma_y']:r['below_error_scale']=True
    return regions


def blocks(lines,pixels,scale,options,manual=None):
    """Primary visual/manual blocks with contained OCR as unranked children.

    An explicit manual list, including [], is authoritative. Never grow a user's
    rectangle to absorb nearby OCR or silently resurrect a deleted region.
    """
    lines=clean_ocr(lines,options);manual_mode=manual is not None
    if manual_mode:
        mains=[dict(r,source='manual',role='primary',ranked=True,original_rect=list(r['rect'])) for r in validate_regions(manual)]
    else:
        mains=[dict(r,id='region-'+str(i),role='primary',ranked=True,confidence=None) for i,r in enumerate(visual_containers(pixels,options),1)]
    children=[];outside=[]
    for line in lines:
        matches=[r for r in mains if intersection(r['rect'],line['rect'])/area(line['rect'])>=options.aoi_containment]
        if matches:
            parent=min(matches,key=lambda r:area(r['rect']));child=dict(line,id='child-'+str(len(children)+1),kind='text_child',source='ocr',role='child',ranked=False,parent_id=parent['id']);children.append(child)
        elif not any(intersection(r['rect'],line['rect'])>0 for r in mains):outside.append(line)
        # OCR boxes crossing multiple containers are not merged across columns.
        # Their text cannot reliably be assigned to one card; keep the cards.
    if not manual_mode:
        for r in text_blocks(outside,scale,options):mains.append(dict(r,role='primary',ranked=True))
    for r in mains:
        contained=[c['text'] for c in children if c['parent_id']==r['id']]
        if contained:r['text']='\n'.join(contained)
        r['algorithm_version']=AOI_ALGORITHM_VERSION
    return validate_regions(mains+children)


class AOIStatistics:
    def __init__(self,regions,scale,grid_width=320,aspect=1.):
        regions=primary_regions(validate_regions(regions))
        self.regions=regions;self.scale=scale;self.total=0.;self.last=None
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
