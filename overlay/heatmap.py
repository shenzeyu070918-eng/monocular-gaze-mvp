"""Non-blocking numeric handoff; all grids, decay and export live off Qt."""
import queue
import threading
import time
import numpy as np
from gaze_core.heatmap import HeatmapStream,UNCALIBRATED,NOTICE,kernel_description
from gaze_core.heatmap_render import render_heatmap,RenderLog


class HeatmapWorker:
    def __init__(self,settings,screen,*,clock_ns=time.perf_counter_ns,exporter=None,render_log=None):
        self.settings=settings;self.screen_size=(screen.width,screen.height);self.clock_ns=clock_ns
        if exporter is None:
            from gaze_core.heatmap_export import export_heatmap
            exporter=export_heatmap
        self.exporter=exporter;self.context=None;self.enabled=False;self.viewing=False;self.closed=False
        self._queue=queue.Queue(settings.heatmap.queue_size);self._stop=threading.Event();self._overflow=threading.Event()
        self._error=None;self._exporting=False;self._export_file=None;self._latest=None
        self._stream=None;self._version=0;self._image=None;self._last_badge=None;self._processed=0
        self._contains_uncalibrated=False
        self._contains_coarse=False
        self._kernel_text=kernel_description(None)
        self._render_log=render_log or RenderLog();self._qt_image=None
        self.thread=threading.Thread(target=self._run,name='gaze-heatmap',daemon=True);self.thread.start()

    def _put(self,command):
        try:self._queue.put_nowait(command);return True
        except queue.Full:
            self._error='热力图队列已满，已停止热力图；推理、录制和点击采集继续。';self.enabled=self.viewing=False
            self._overflow.set()
            return False

    def configure(self,context):
        # Contexts are owned immutable snapshots, constructed only on changes.
        self.context=context
        self._kernel_text=kernel_description(context['kernel'])
        if self.enabled and not context['calibrated'] and not self.settings.heatmap.allow_uncalibrated:
            self.enabled=self.viewing=False;self._error=UNCALIBRATED
        return self._put(('configure',context))

    def set_enabled(self,enabled):
        if self.closed:return False
        if enabled and (self.context is None or not self.context['calibrated']) and not self.settings.heatmap.allow_uncalibrated:
            self._error=UNCALIBRATED;self.enabled=self.viewing=False;return False
        self._error=None;self.enabled=bool(enabled)
        self.viewing=self.enabled and self.settings.heatmap.display_mode=='continuous'
        if self.viewing:self._latest=None
        return self._put(('enable',self.enabled,self.clock_ns()))

    def set_viewing(self,viewing):
        if self.closed:return False
        if viewing and not self.enabled:
            self._error='请先开启热力图后台累加，再查看。';return False
        if self.viewing==bool(viewing):return True
        self.viewing=bool(viewing)
        if self.viewing:self._latest=None  # First view must use a fresh snapshot.
        return self._put(('view',self.viewing))

    def observe(self,prediction):
        if not self.enabled or self.closed:return True
        data=prediction.data
        numeric={key:data.get(key) for key in ('valid','status','matrix_revision')}
        for key in ('raw','origin_m'):
            value=data.get(key);numeric[key]=None if value is None else tuple(value)
        self._put(('frame',numeric,int(prediction.captured_ns)))
        return True  # Heatmap failure must never stop the existing task sink.

    def interrupt(self):return self._put(('interrupt',)) if not self.closed else False

    def change_screen(self,screen):
        self.enabled=self.viewing=False;self.context=None;self._latest=None
        self.screen_size=(screen.width,screen.height)
        return self._put(('screen',self.screen_size))

    def clear(self):
        self._error=None;return self._put(('clear',self.clock_ns()))

    def export(self,directory=None):
        if self.closed or self._exporting:return False
        self._exporting=True
        if not self._put(('export',directory)):
            self._exporting=False;return False
        return True

    def state(self):
        latest=self._latest
        uncalibrated=self.context is None or not self.context['calibrated'] or self._contains_uncalibrated
        label='热力图：'+('开启（后台累加）' if self.enabled else '关闭')
        coarse=(self.context is not None and self.context['phase'].get('group')=='C') or self._contains_coarse
        if self.enabled:label+=' · '+('正在查看' if self.viewing else '未查看')
        if self.enabled and uncalibrated:label+=' · 未校准'
        elif self.enabled and coarse:label+=' · 粗校准'
        if self._error:label+=' · '+self._error
        if self._exporting:label+=' · 正在导出'
        elif self._export_file:label+=' · 已导出 '+self._export_file
        return dict(enabled=self.enabled,viewing=self.viewing,uncalibrated=uncalibrated,coarse_calibrated=coarse,error=self._error,label=label,
                    exporting=self._exporting,export_file=self._export_file,latest=latest,processed=self._processed,
                    has_session=self._stream is not None,kernel_text=self._kernel_text,
                    render_log=None if self._render_log.path is None else str(self._render_log.path),last_render=self._render_log.last)

    def _new(self,start_ns):
        self._stream=HeatmapStream(self.settings.heatmap,self.settings.fixation,self.screen_size,decay=True,start_ns=start_ns)
        if self.context is not None:self._stream.configure(self.context)
        self._contains_uncalibrated=False
        self._contains_coarse=False

    def _publish(self,now_ns):
        if self._stream is None:return
        started=time.perf_counter_ns()
        snapshot=self._stream.snapshot(now_ns)
        options=self.settings.heatmap
        rgba=render_heatmap(snapshot['grid'],self.screen_size,max_opacity=options.max_opacity,
                            alpha_threshold=options.alpha_threshold,render_scale=options.render_scale,dither_strength=options.dither_strength)
        self._contains_uncalibrated=any(p['context'].get('group')=='A' for p in snapshot['phases'])
        coarse=(self.context is not None and self.context['phase'].get('group')=='C') or self._contains_coarse
        badge=(self.context is None or not self.context['calibrated'] or self._contains_uncalibrated,coarse)
        if self._image is None or not np.array_equal(rgba,self._image) or badge!=self._last_badge:
            self._version+=1;self._image=rgba;rgba.setflags(write=False);self._last_badge=badge
            # Expensive alpha conversion and ownership copy belong to the
            # render worker; the GUI receives an implicitly shared QImage.
            from PySide6.QtGui import QImage
            height,width=rgba.shape[:2]
            self._qt_image=QImage(rgba.data,width,height,rgba.strides[0],QImage.Format.Format_RGBA8888).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._render_log.record((time.perf_counter_ns()-started)/1e6,rgba,options)
        self._latest=dict(version=self._version,rgba=self._image,image=self._qt_image,uncalibrated=badge[0],coarse_calibrated=coarse)

    def _run(self):
        running=False;next_update=0
        while not self._stop.is_set() or not self._queue.empty():
            try:
                command=self._queue.get(timeout=1/self.settings.heatmap.update_hz)
            except queue.Empty:command=None
            fence=None
            try:
                if self._overflow.is_set():
                    self._overflow.clear();running=False
                    if self._stream is not None:self._stream.interrupt();self._stream.skip('queue_overflow')
                if command is not None:
                    kind=command[0]
                    if kind=='configure':
                        if self._stream is not None:self._stream.configure(command[1])
                    elif kind=='screen':
                        running=False;self._stream=None;self._image=self._qt_image=None
                        self._contains_uncalibrated=self._contains_coarse=False
                        self._version+=1;self._last_badge=None
                        self._kernel_text=kernel_description(None)
                    elif kind=='enable':
                        running=command[1]
                        if self._stream is None:self._new(command[2])
                        else:self._stream.interrupt()
                        next_update=0
                    elif kind=='view':
                        if command[1]:next_update=0
                    elif kind=='frame' and running:
                        if command[2]>=self._stream.start_ns:self._stream.feed(command[1],command[2]);self._processed+=1
                    elif kind=='interrupt':
                        if self._stream is not None:self._stream.interrupt()
                    elif kind=='clear':self._new(command[1]);next_update=0
                    elif kind=='export':
                        try:
                            if self._stream is None:raise ValueError('尚无热力图会话，请先开启热力图')
                            snapshot=self._stream.snapshot(self.clock_ns())
                            uncalibrated=(self.context is None or not self.context['calibrated'] or
                                          any(p['context'].get('group')=='A' for p in snapshot['phases']))
                            snapshot.update(source='live_accumulator',uncalibrated=uncalibrated,
                                            coarse_calibrated=(self.context is not None and self.context['phase'].get('group')=='C') or any(p['context'].get('group')=='C' for p in snapshot['phases']),
                                            notice=NOTICE,screen=dict(width_px=self.screen_size[0],height_px=self.screen_size[1]))
                            self._export_file=str(self.exporter(snapshot,directory=command[1]))
                        finally:self._exporting=False
                    # Testable drain fence without sleeping or accessing grids on Qt.
                    elif kind=='barrier':fence=command[1]
                now=self.clock_ns()
                if self._stream is not None:
                    # Hidden collection only expires gaps; no grid snapshots,
                    # RGBA buffers or window-facing image versions are made.
                    self._stream.expire(now)
                    phases=self._stream.accumulator.phases
                    if phases and phases[-1]['context'].get('group')=='A':self._contains_uncalibrated=True
                    if phases and phases[-1]['context'].get('group')=='C':self._contains_coarse=True
                if self.viewing and self.enabled and not self.closed and now>=next_update:
                    self._publish(now);next_update=now+round(1e9/self.settings.heatmap.view_update_hz)
            except Exception as exc:
                self._error='热力图失败：'+str(exc);self.enabled=self.viewing=running=False
                if self._stream is not None:self._stream.interrupt()
            finally:
                if fence is not None:fence.set()
        if self._stream is not None:self._stream.interrupt()
        self._render_log.close()

    def close(self):
        if self.closed and not self.thread.is_alive():return
        self.enabled=self.viewing=False;self.closed=True;self._stop.set()
        self.thread.join(self.settings.recording.close_timeout_s)
        if self.thread.is_alive():raise RuntimeError('热力图后台线程尚未退出，请等待导出完成后重试')
