"""Duration-weighted fixation heatmaps. No Qt, camera, screenshot or model I/O.

Only completed fixations enter the accumulated grids. An ongoing fixation is
projected into a snapshot, never appended repeatedly. Exponential decay is
integrated over each segment's capture-clock interval, not its display ticks.
"""
from copy import deepcopy
import math
import numpy as np

from .config import HeatmapSettings
from .devices import finite
from .display import calibration_matrix
from .fixation import FixationDetector

NOTICE='热力图表示「看了屏幕的哪个区域」，不表示「看了什么内容」；窗口切换和滚动会让同一位置对应不同内容。'
UNCALIBRATED='未校准，位置可能偏差约 10 cm'


def kernel_scale(geometry, screen_size, radius_cm, fallback_radius_px, source):
    """ONE conversion for live/offline/export: sigma = ring error radius.

    On measured screens sigma_cm = matching B mean error (or the ring's
    illustrative cm fallback), NOT radius/2 or radius/3. Thus heatmaps never
    claim a sharper scale than the gaze ring. Unmeasured screens use the same
    illustrative logical-pixel radius as the ring, never guessed centimetres.
    """
    width,height=screen_size
    if width<=0 or height<=0:raise ValueError('逻辑屏幕尺寸必须为正数')
    if geometry.measured and geometry.width_cm and geometry.height_cm:
        radius=finite(radius_cm,'radius_cm',positive=True)
        return dict(sigma_x=radius/geometry.width_cm,sigma_y=radius/geometry.height_cm,
                    sigma_cm=radius,sigma_px=None,source=source,definition='sigma = error_radius_cm')
    radius=finite(fallback_radius_px,'fallback_radius_px',positive=True)
    return dict(sigma_x=radius/width,sigma_y=radius/height,sigma_cm=None,sigma_px=radius,
                source='configured_logical_pixels',definition='sigma = illustrative_ring_radius_px')


def heatmap_position(center,scale):
    """Shared live/offline *heatmap-only* projection; never alter raw/I-VT data.

    Distance to the nearest point of the screen rectangle is Euclidean in cm
    (or logical pixels): hypot(dx/sigma_x, dy/sigma_y) <= 1. Thus a diagonal
    corner error cannot pass merely because each individual axis is < sigma.
    The tolerance is exactly the existing kernel's sigma, with no new radius.
    """
    x,y=[finite(v,'center') for v in center]
    sx,sy=[finite(scale[k],k,positive=True) for k in ('sigma_x','sigma_y')]
    nearest=[min(1.,max(0.,x)),min(1.,max(0.,y))]
    distance=math.hypot((x-nearest[0])/sx,(y-nearest[1])/sy)
    if distance==0:return nearest,'inside'
    # Include the exact boundary despite floating point representation error.
    if distance<=1 or math.isclose(distance,1.,rel_tol=1e-12,abs_tol=1e-12):return nearest,'edge_clamped'
    return None,'outside'


class RegionStatistics:
    def __init__(self,regions=None):
        self.regions=deepcopy(regions) if regions is not None else [
            dict(id=f'{y+1}-{x+1}',rect=[x/3,y/3,(x+1)/3,(y+1)/3]) for y in range(3) for x in range(3)]
        self.rows={r['id']:dict(id=r['id'],rect=r['rect'],dwell_s=0.,first_fixation_s=None,entries=0,fixations=0) for r in self.regions}
        self.total_s=0.;self.unassigned_s=0.;self.last=None

    def interrupt(self):self.last=None

    def add(self,segment,start_s):
        x,y=segment['center'];duration=segment['end_s']-segment['start_s'];self.total_s+=duration
        name=None
        for region in self.regions:
            a,b,c,d=region['rect']
            if a<=x and (x<c or c==1 and x==1) and b<=y and (y<d or d==1 and y==1):name=region['id'];break
        if name is None:self.unassigned_s+=duration
        else:
            row=self.rows[name];row['dwell_s']+=duration;row['fixations']+=1
            if row['first_fixation_s'] is None:row['first_fixation_s']=max(0.,segment['start_s']-start_s)
            if self.last!=name:row['entries']+=1
        self.last=name

    def snapshot(self):
        return dict(total_fixation_s=self.total_s,unassigned_s=self.unassigned_s,
                    regions=[dict(row,dwell_fraction=row['dwell_s']/self.total_s if self.total_s else None) for row in self.rows.values()])


class HeatmapAccumulator:
    def __init__(self,options,screen_size,*,decay=False,start_s=0.):
        self.options=options;self.start_s=start_s;self.decay=decay
        width=options.grid_width;height=max(1,round(width*screen_size[1]/screen_size[0]))
        if height>4096:raise ValueError('热力图网格高度过大，请检查屏幕比例')
        self.x,self.y=np.meshgrid((np.arange(width)+.5)/width,(np.arange(height)+.5)/height)
        self.total=np.zeros((height,width),float);self.faded=self.total.copy();self.at_s=start_s
        self.regions=RegionStatistics(options.regions);self.outside_s=0.;self.edge_clamped_s=0.;self.segment_count=0
        self.phases=[];self._phase_key=None

    def kernel(self,center,scale):
        point,_=heatmap_position(center,scale)
        if point is None:return None
        x,y=point
        # Normalize visible Gaussian mass: edge fixations retain their duration.
        exponent=-.5*(((self.x-x)/scale['sigma_x'])**2+((self.y-y)/scale['sigma_y'])**2)
        exponent-=exponent.max();weights=np.exp(exponent)
        return weights/weights.sum()

    def _advance(self,now_s):
        now_s=max(self.at_s,finite(now_s,'now_s'))
        if self.decay:self.faded*=2**(-(now_s-self.at_s)/self.options.half_life_s)
        self.at_s=now_s

    def _weight(self,segment,now_s):
        duration=segment['end_s']-segment['start_s']
        if not self.decay:return duration
        rate=math.log(2)/self.options.half_life_s
        return -math.expm1(-rate*duration)/rate*math.exp(-rate*max(0.,now_s-segment['end_s']))

    def add(self,segment,scale,*,phase=None):
        start,end=[finite(segment[k],k) for k in ('start_s','end_s')]
        if end<start:raise ValueError('注视段结束时间早于开始')
        if end==start:return
        self._advance(end)
        key=repr((phase,scale))
        if key!=self._phase_key:
            self._phase_key=key;self.phases.append(dict(start_s=start,context=deepcopy(phase),kernel=deepcopy(scale)))
        kernel=self.kernel(segment['center'],scale)
        if kernel is None:
            self.outside_s+=end-start;self.regions.interrupt();return
        self.total+=kernel*(end-start);self.faded+=kernel*self._weight(segment,self.at_s)
        point,placement=heatmap_position(segment['center'],scale)
        if placement=='edge_clamped':self.edge_clamped_s+=end-start
        self.regions.add(dict(segment,center=point),self.start_s);self.segment_count+=1

    def snapshot(self,now_s,*,preview=None,scale=None,phase=None):
        self._advance(now_s)
        grid=(self.faded if self.decay else self.total).copy();total=self.total.copy()
        regions=self.regions;outside=self.outside_s;clamped=self.edge_clamped_s;count=self.segment_count
        if preview is not None:
            kernel=self.kernel(preview['center'],scale)
            if kernel is not None:
                grid+=kernel*self._weight(preview,self.at_s);total+=kernel*(preview['end_s']-preview['start_s'])
                point,placement=heatmap_position(preview['center'],scale)
                regions=deepcopy(regions);regions.add(dict(preview,center=point),self.start_s);count+=1
                if placement=='edge_clamped':clamped+=preview['end_s']-preview['start_s']
            else:outside+=preview['end_s']-preview['start_s']
        return dict(grid=grid,total_grid=total,regions=regions.snapshot(),outside_s=outside,edge_clamped_s=clamped,fixation_count=count,
                    edge_policy='中心到最近屏幕点的距离≤1 sigma时仅为热图夹回；超过1 sigma计outside_s。角落按厘米/逻辑像素欧氏距离判断。',
                    start_s=self.start_s,snapshot_s=self.at_s,decay=self.decay,
                    half_life_s=self.options.half_life_s if self.decay else None,
                    unit='decay_weighted_seconds_per_cell' if self.decay else 'seconds_per_cell',
                    phases=deepcopy(self.phases),ongoing_fixation=preview is not None,
                    current_context=deepcopy(phase),current_kernel=deepcopy(scale),notice=NOTICE,
                    visualization=dict(palette='transparent_yellow_red',normalization='current_grid_max',
                                       alpha_threshold=self.options.alpha_threshold,max_opacity=self.options.max_opacity,
                                       render_scale=self.options.render_scale,dither_strength=self.options.dither_strength))


class HeatmapStream:
    """Raw numeric frames + versioned contexts -> M2 segments and snapshots."""
    def __init__(self,options,fixation,screen_size,*,decay=True,start_ns=0):
        self.options=options;self.screen_size=screen_size;self.start_ns=start_ns
        self.accumulator=HeatmapAccumulator(options,screen_size,decay=decay,start_s=0.)
        self.detector=FixationDetector(fixation);self.context=None;self.preview=None;self.last_ns=None
        self.skipped={};self.fallback_frames=0

    def skip(self,reason):self.skipped[reason]=self.skipped.get(reason,0)+1

    def interrupt(self):
        if self.context is not None:
            for segment in self.detector.reset():self.accumulator.add(segment,self.context['kernel'],phase=self.context['phase'])
        else:self.detector.reset()
        self.preview=None;self.accumulator.regions.interrupt()

    def configure(self,context):
        self.interrupt();self.context=context

    def feed(self,data,capture_ns):
        if self.last_ns is not None and capture_ns<=self.last_ns:self.skip('nonincreasing_capture_ns');return
        if self.last_ns is not None and (capture_ns-self.last_ns)/1e6>self.detector.settings.max_gap_ms:self.interrupt()
        self.last_ns=capture_ns
        context=self.context
        if context is None:self.skip('missing_calibration_context');return
        if data.get('matrix_revision')!=context['revision']:
            self.interrupt();self.skip('matrix_revision_mismatch');return
        if context['matrix'] is None and not self.options.allow_uncalibrated:
            self.interrupt();self.skip('uncalibrated');return
        if not data.get('valid') or data.get('status')!='tracking' or data.get('raw') is None:
            self.interrupt();self.skip(data.get('status','invalid'));return
        raw=np.asarray(data['raw'],float);matrix=calibration_matrix(context['matrix'])
        point=np.append(raw,1)@matrix if matrix is not None else raw
        result=self.detector.update(point,(capture_ns-self.start_ns)/1e9,origin_m=data.get('origin_m'),geometry=context['geometry'])
        for segment in result['finished_fixations']:self.accumulator.add(segment,context['kernel'],phase=context['phase'])
        self.preview=result['current_fixation']
        if result['velocity_basis']!='angular':self.fallback_frames+=1

    def expire(self,now_ns):
        # No result can extend a fixation beyond its last actual capture.
        if self.last_ns is not None and (now_ns-self.last_ns)/1e6>self.detector.settings.max_gap_ms:self.interrupt()

    def snapshot(self,now_ns):
        self.expire(now_ns)
        result=self.accumulator.snapshot((now_ns-self.start_ns)/1e9,preview=self.preview,
                 scale=None if self.context is None else self.context['kernel'],phase=None if self.context is None else self.context['phase'])
        result.update(skipped=dict(self.skipped),fallback_frames=self.fallback_frames)
        return result


def kernel_description(scale):
    if scale is None:return '核尺度 sigma：等待相机条件'
    if scale['sigma_cm'] is not None:
        value=f"{scale['sigma_cm']:.3f} cm"
        source=('最近匹配 C 粗校准核对平均误差' if scale['source']=='matching_C_check' else
                '最近匹配 B 核对平均误差' if scale['source']=='matching_B_check' else '配置示意值（非实测精度）')
    else:
        value=f"{scale['sigma_px']:.3f} 逻辑像素";source='配置示意值（屏幕未实测）'
    return '核尺度 sigma = '+value+' · 来源：'+source


def rgba_grid(grid,max_opacity,alpha_threshold=.15,*,dither_strength=0.):
    """Relative density -> transparent yellow/red. No dark or cool low end.

    Only display normalization changes; numeric duration/decay stays untouched.
    Uniform scaling of an entire grid keeps relative colors unchanged.
    Between threshold and 2*threshold a smoothstep envelope fades alpha into
    its normal linear value, with zero envelope slope at both joins.
    """
    opacity=finite(max_opacity,'max_opacity',positive=True)
    threshold=finite(alpha_threshold,'alpha_threshold')
    dither=finite(dither_strength,'dither_strength')
    if not 0<=dither<=1:raise ValueError('dither_strength 必须在 [0,1]')
    if opacity>.4 or not 0<=threshold<1:raise ValueError('热图不透明度不得超过0.4，透明阈值必须在[0,1)')
    grid=np.asarray(grid,float);peak=float(grid.max()) if grid.size else 0.
    if not np.isfinite(grid).all() or (grid<0).any():raise ValueError('热图必须是非负有限数值')
    value=np.zeros_like(grid) if peak<=0 else np.clip(grid/peak,0,1)
    strength=np.clip((value-threshold)/(1-threshold),0,1)
    # Keep the existing color/opacity scale above the fade band. Only alpha
    # gets the envelope; threshold=0 has no band and needs no division.
    fade=1.
    if threshold>0:
        progress=np.clip((value-threshold)/threshold,0,1)
        fade=progress*progress*(3-2*progress)
    rgba=np.empty((*grid.shape,4),np.uint8)
    rgba[...,0]=255;rgba[...,1]=np.rint(255*(1-strength)).astype(np.uint8);rgba[...,2]=0
    alpha=255*opacity*strength*fade
    if dither:
        # Fixed 8x8 Bayer tile, centred at zero. A full strength unit spans
        # one alpha LSB peak-to-peak, never one unit of normalized opacity.
        tile=np.array([[0,48,12,60,3,51,15,63],[32,16,44,28,35,19,47,31],
                       [8,56,4,52,11,59,7,55],[40,24,36,20,43,27,39,23],
                       [2,50,14,62,1,49,13,61],[34,18,46,30,33,17,45,29],
                       [10,58,6,54,9,57,5,53],[42,26,38,22,41,25,37,21]],float)
        noise=((tile+.5)/64-.5)*dither
        alpha=alpha+noise[np.arange(grid.shape[0])[:,None]%8,np.arange(grid.shape[1])[None,:]%8]
    # Thresholded cells remain exactly transparent; dithering cannot exceed
    # max_opacity, turn a zero field visible, or perturb stored durations.
    rgba[...,3]=np.where(value<=threshold,0,np.floor(np.clip(alpha,0,255*opacity))).astype(np.uint8)
    return rgba
