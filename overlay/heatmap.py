"""Non-blocking numeric handoff; all grids, decay and export live off Qt."""
import queue
import threading
import time
import numpy as np
from gaze_core.heatmap import HeatmapStream,rgba_grid,UNCALIBRATED,NOTICE


class HeatmapWorker:
    def __init__(self,settings,screen,*,clock_ns=time.perf_counter_ns,exporter=None):
        self.settings=settings;self.screen_size=(screen.width,screen.height);self.clock_ns=clock_ns
        if exporter is None:
            from gaze_core.heatmap_export import export_heatmap
            exporter=export_heatmap
        self.exporter=exporter;self.context=None;self.enabled=False;self.closed=False
        self._queue=queue.Queue(settings.heatmap.queue_size);self._stop=threading.Event();self._overflow=threading.Event()
        self._error=None;self._exporting=False;self._export_file=None;self._latest=None
        self._stream=None;self._version=0;self._image=None;self._last_badge=None;self._processed=0
        self._contains_uncalibrated=False
        self.thread=threading.Thread(target=self._run,name='gaze-heatmap',daemon=True);self.thread.start()

    def _put(self,command):
        try:self._queue.put_nowait(command);return True
        except queue.Full:
            self._error='热力图队列已满，已停止热力图；推理、录制和点击采集继续。';self.enabled=False
            self._overflow.set()
            return False

    def configure(self,context):
        # Contexts are owned immutable snapshots, constructed only on changes.
        self.context=context
        if self.enabled and not context['calibrated'] and not self.settings.heatmap.allow_uncalibrated:
            self.enabled=False;self._error=UNCALIBRATED
        return self._put(('configure',context))

    def set_enabled(self,enabled):
        if self.closed:return False
        if enabled and (self.context is None or not self.context['calibrated']) and not self.settings.heatmap.allow_uncalibrated:
            self._error=UNCALIBRATED;self.enabled=False;return False
        self._error=None;self.enabled=bool(enabled)
        return self._put(('enable',self.enabled,self.clock_ns()))

    def observe(self,prediction):
        if not self.enabled or self.closed:return True
        data=prediction.data
        numeric={key:data.get(key) for key in ('valid','status','matrix_revision')}
        for key in ('raw','origin_m'):
            value=data.get(key);numeric[key]=None if value is None else tuple(value)
        self._put(('frame',numeric,int(prediction.captured_ns)))
        return True  # Heatmap failure must never stop the existing task sink.

    def interrupt(self):return self._put(('interrupt',)) if not self.closed else False

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
        label='热力图：'+('开启' if self.enabled else '关闭')+(' · 未校准' if self.enabled and uncalibrated else '')
        if self._error:label+=' · '+self._error
        if self._exporting:label+=' · 正在导出'
        elif self._export_file:label+=' · 已导出 '+self._export_file
        return dict(enabled=self.enabled,uncalibrated=uncalibrated,error=self._error,label=label,
                    exporting=self._exporting,export_file=self._export_file,latest=latest,processed=self._processed)

    def _new(self,start_ns):
        self._stream=HeatmapStream(self.settings.heatmap,self.settings.fixation,self.screen_size,decay=True,start_ns=start_ns)
        if self.context is not None:self._stream.configure(self.context)
        self._contains_uncalibrated=False

    def _publish(self,now_ns):
        if self._stream is None:return
        snapshot=self._stream.snapshot(now_ns)
        rgba=rgba_grid(snapshot['grid'],self.settings.heatmap.max_opacity)
        self._contains_uncalibrated=any(p['context'].get('group')=='A' for p in snapshot['phases'])
        badge=self.context is None or not self.context['calibrated'] or self._contains_uncalibrated
        if self._image is None or not np.array_equal(rgba,self._image) or badge!=self._last_badge:
            self._version+=1;self._image=rgba;rgba.setflags(write=False);self._last_badge=badge
        self._latest=dict(version=self._version,rgba=self._image,uncalibrated=badge)

    def _run(self):
        running=False;next_update=0;dirty=False
        while not self._stop.is_set() or not self._queue.empty():
            try:
                command=self._queue.get(timeout=1/self.settings.heatmap.update_hz)
            except queue.Empty:command=None
            try:
                if self._overflow.is_set():
                    self._overflow.clear();running=False
                    if self._stream is not None:self._stream.interrupt();self._stream.skip('queue_overflow')
                if command is not None:
                    kind=command[0]
                    if kind=='configure':
                        if self._stream is not None:self._stream.configure(command[1])
                        dirty=True
                    elif kind=='enable':
                        running=command[1]
                        if self._stream is None:self._new(command[2])
                        else:self._stream.interrupt()
                        dirty=True
                    elif kind=='frame' and running:
                        if command[2]>=self._stream.start_ns:self._stream.feed(command[1],command[2]);self._processed+=1;dirty=True
                    elif kind=='interrupt':
                        if self._stream is not None:self._stream.interrupt()
                        dirty=True
                    elif kind=='clear':self._new(command[1]);dirty=True
                    elif kind=='export':
                        try:
                            if self._stream is None:raise ValueError('尚无热力图会话，请先开启热力图')
                            snapshot=self._stream.snapshot(self.clock_ns())
                            uncalibrated=(self.context is None or not self.context['calibrated'] or
                                          any(p['context'].get('group')=='A' for p in snapshot['phases']))
                            snapshot.update(source='live_accumulator',uncalibrated=uncalibrated,
                                            notice=NOTICE,screen=dict(width_px=self.screen_size[0],height_px=self.screen_size[1]))
                            self._export_file=str(self.exporter(snapshot,directory=command[1]))
                        finally:self._exporting=False
                    # Testable drain fence without sleeping or accessing grids on Qt.
                    elif kind=='barrier':
                        self._publish(self.clock_ns());command[1].set()
                now=self.clock_ns()
                fading=self._stream is not None and bool(np.any(self._stream.accumulator.faded))
                if now>=next_update and (dirty or running and fading):
                    self._publish(now);dirty=False;next_update=now+round(1e9/self.settings.heatmap.update_hz)
            except Exception as exc:
                self._error='热力图失败：'+str(exc);self.enabled=running=False
                if self._stream is not None:self._stream.interrupt()
        if self._stream is not None:self._stream.interrupt()

    def close(self):
        if self.closed and not self.thread.is_alive():return
        self.enabled=False;self.closed=True;self._stop.set()
        self.thread.join(self.settings.recording.close_timeout_s)
        if self.thread.is_alive():raise RuntimeError('热力图后台线程尚未退出，请等待导出完成后重试')
