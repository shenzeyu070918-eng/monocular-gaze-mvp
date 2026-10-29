"""One worker owns capture, files and finalization; inference only queues numbers."""
from copy import deepcopy
from collections import deque
from dataclasses import asdict
import hashlib,io,json,platform,queue,threading,time
from pathlib import Path
import numpy as np
from PIL import Image,ImageOps
from .binding import frame_record
from .contracts import CapturePaused
from .scenes import SceneDetector
from .storage import SessionStore,delete_session
from .processing import process_session


class AttentionSession:
    def __init__(self,settings,screen,*,backend_factory=None,processor=process_session,clock_ns=time.perf_counter_ns,root=None):
        from gaze_core.config import ROOT
        self.settings,self.screen,self.clock_ns=settings,screen,clock_ns;self.processor=processor
        self.root=Path(root if root is not None else settings.attention.output_dir)
        if not self.root.is_absolute():self.root=ROOT/self.root
        if root is None and not self.root.resolve().is_relative_to((ROOT/'data/attention').resolve()):raise ValueError('注意力数据只能放在本机data/attention/下')
        self.backend_factory=backend_factory;self._commands=queue.Queue(settings.attention.queue_size)
        self._cancel=threading.Event();self._wake=threading.Event();self._shutdown=False;self._epoch=0
        self.state='idle';self.error=None;self.path=None;self.summary=None;self.desired=False;self.user_paused=False;self.blocked=None
        self.scene_count=0;self.mode=None;self.started_ns=None;self.profile_id=None;self._store=None;self._backend=None
        self._effective_state=None;self._detector=None;self._metadata=None;self.timings=[];self._end=None;self._stop_ns=None
        self._sample_count=0;self._first_sample_ns=None;self._last_sample_ns=None;self._baseline=None;self._accepting_frames=False
        self.thread=threading.Thread(target=self._run,name='attention-local',daemon=True);self.thread.start()

    @property
    def active(self):return self.state in ('starting','recording','paused')
    @property
    def busy(self):return self.active or self.state in ('finalizing','deleting')

    def _put(self,command):
        try:self._commands.put_nowait(command);self._wake.set();return True
        except queue.Full:
            self.error='注意力数值队列已满，已停止会话；不能丢弃帧后仍报告成功。';self.desired=False;self._end='queue_overflow';self._epoch+=1;self._wake.set();return False

    def start(self,profile,conditions,kernel,*,mode='screen',stimulus=None):
        if self._shutdown:raise ValueError('注意力后台已关闭')
        if self.busy:raise ValueError('同一时间只允许一个注意力会话')
        from gaze_core.profiles import PYTHON_COORDINATE_VERSION
        from gaze_core.display import calibration_matrix
        if profile is None or profile.get('pipeline')!='python' or profile.get('source')!='nine_point' or profile.get('group')!='B' or profile.get('coordinate_version')!=PYTHON_COORDINATE_VERSION or profile.get('kind')=='click_offset' or not isinstance(conditions,dict) or profile.get('conditions')!=conditions:
            raise ValueError('请先完成并启用条件匹配的 Python 九点校准；粗校准不能开始注意力会话。')
        if calibration_matrix(profile.get('matrix')) is None:raise ValueError('九点矩阵不可用，请重新校准')
        for key in ('sigma_x','sigma_y'):
            if not np.isfinite(kernel[key]) or kernel[key]<=0:raise ValueError('误差核尺度必须为有限正数')
        if mode not in ('screen','stimulus'):raise ValueError('未知注意力模式')
        if mode=='stimulus' and stimulus is None:raise ValueError('请先选择PNG/JPG刺激图片')
        self._cancel.clear();self._epoch+=1;self.error=None;self.summary=None;self.path=None;self.scene_count=0;self.timings=[]
        self._accepting_frames=False;self.user_paused=False;self.blocked=None;self.mode=mode;self.profile_id=profile.get('calibration_id')
        self.started_ns=self.clock_ns();self._stop_ns=None;self._sample_count=0;self._first_sample_ns=self._last_sample_ns=None
        metadata=dict(mode=mode,start_ns=self.started_ns,wall_started_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),system=platform.platform(),
                      program_version=self._version(),geometry=asdict(self.settings.geometry),fixation=asdict(self.settings.fixation),heatmap=asdict(self.settings.heatmap),
                      attention={k:v for k,v in asdict(self.settings.attention).items() if k not in ('excluded_apps','manual_aoi')},screen=asdict(self.screen),analysis_size=[self.screen.width,self.screen.height],
                      profile=deepcopy(profile),kernel=deepcopy(kernel),time_source='perf_counter_ns; snapshot request/receive interval',
                      privacy=dict(local_only=True,cursor=False,window_titles=False,ocr_during_capture=False))
        self._metadata=metadata;self.state='starting';self.desired=True
        queued=self._put(('start',metadata,stimulus,self._epoch))
        if not queued:self.state='error'
        return queued

    @staticmethod
    def _version():
        from gaze_core.config import ROOT
        import subprocess
        try:return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,timeout=2).strip()
        except Exception:return 'unknown'

    def begin_baseline(self,seconds=30):
        self._baseline=(self.clock_ns(),int(seconds*1e9),deque(maxlen=10000))

    def baseline_latency(self):
        item=self._baseline;self._baseline=None
        values=[] if item is None else list(item[2])
        return dict(count=len(values),median_ms=float(np.median(values)) if values else None,p90_ms=float(np.percentile(values,90)) if values else None,definition='OpenCV read返回至推理完成，不含曝光、驱动缓冲或显示')

    def observe(self,prediction):
        baseline=self._baseline
        if baseline is not None and baseline[0]<=prediction.captured_ns<baseline[0]+baseline[1]:baseline[2].append((prediction.completed_ns-prediction.captured_ns)/1e6)
        if not self._accepting_frames or not self.active or not self.desired or self.user_paused or self.blocked or prediction.captured_ns<=self.started_ns:return
        camera=prediction.data.get('camera')
        expected=(self._metadata or {}).get('profile',{}).get('conditions',{}).get('camera')
        if camera is not None and expected is not None and any(camera.get(k)!=expected.get(k) for k in ('index','width_px','height_px','grayscale','intrinsics_source','intrinsics_reason','undistort_applied') if k in expected):
            self.block('camera_conditions_changed_restart_session',since_ns=int(prediction.captured_ns));return
        try:self._put(('frame',frame_record(prediction),self._epoch))
        except Exception as exc:self.error='注意力数值格式失败：'+str(exc);self.stop('invalid_numeric')

    def pause(self):
        if not self.active:return
        self.user_paused=not self.user_paused;self._epoch+=1
        self._put(('state','user_paused' if self.user_paused else 'recording',self.clock_ns()))

    def block(self,reason,*,since_ns=None):
        if reason==self.blocked:return
        self.blocked=reason;self._epoch+=1
        if self.active:self._put(('state',reason or ('user_paused' if self.user_paused else 'recording'),self.clock_ns() if since_ns is None else since_ns))

    def stop(self,reason='user_ended'):
        if not self.active or not self.desired:return
        self.desired=False;self._epoch+=1;self._end=reason;self._stop_ns=self.clock_ns();self._wake.set()

    def delete(self):
        if self.path is None:return
        self.state='deleting';self._cancel.set();self.desired=False;self._epoch+=1;self._end='deleted';self._put(('delete',))

    def close(self):
        self._shutdown=True;self.desired=False;self._epoch+=1;self._end='overlay_closed';self._wake.set()
        self.thread.join(150)
        if self.thread.is_alive():raise RuntimeError('注意力后台尚未结束，不能报告资源已释放')

    def _pending_transition(self,end_ns):
        if self._store is None or self._detector is None or not self._detector.pending:return
        first=self._detector.last_old or self._detector.pending[0][0]
        if end_ns>first.time_ns:self._store.append('scenes.jsonl',dict(type='transition_pending',start_ns=first.time_ns,end_ns=end_ns),final=True)

    def _state(self,status,now=None):
        if self._store is None or status==self._effective_state:return
        self._effective_state=status;self.state='recording' if status=='recording' else 'paused'
        self._store.append('scenes.jsonl',dict(type='state',time_ns=self.clock_ns() if now is None else now,status=status))
        if status!='recording':
            self._pending_transition(self.clock_ns() if now is None else now)
            if self._detector:self._detector.interrupt()
            self._release_backend()

    def _release_backend(self):
        if self._backend is not None:self._backend.close();self._backend=None

    def _begin(self,metadata,stimulus):
        self._store=SessionStore(self.root,metadata,self.settings.attention.max_disk_mb*1024*1024);self.path=self._store.path
        self._store.write_bytes('gaze.jsonl',b'')
        self._detector=SceneDetector(self.settings.attention);self._effective_state=None;self._state('recording',metadata['start_ns'])
        if self.mode=='stimulus':
            path=Path(stimulus['path'])
            if path.suffix.lower() not in ('.png','.jpg','.jpeg'):raise ValueError('首版刺激物只支持PNG/JPG')
            if path.stat().st_size>self.settings.attention.stimulus_max_mb*1024*1024:raise ValueError('刺激图片超过文件大小上限')
            raw=path.read_bytes()
            if stimulus.get('sha256') and hashlib.sha256(raw).hexdigest()!=stimulus['sha256']:raise ValueError('图片在显示后被修改，请重新选择')
            source=Image.open(io.BytesIO(raw))
            if source.width*source.height>self.settings.attention.stimulus_max_pixels:raise ValueError('刺激图片像素数超过配置上限')
            pixels=np.asarray(ImageOps.exif_transpose(source).convert('RGB'))
            digest=self._store.write_image('scenes/000001.png',pixels)
            self._store.metadata.update(stimulus=dict(sha256=hashlib.sha256(raw).hexdigest(),screen_rect=stimulus['screen_rect']),analysis_size=[pixels.shape[1],pixels.shape[0]],kernel=stimulus['kernel'])
            self._store.write_json('session.json',self._store.metadata)
            self._store.append('scenes.jsonl',dict(type='scene',scene_id='000001',start_ns=metadata['start_ns'],confirmed_ns=self.clock_ns(),image='scenes/000001.png',sha256=digest,app_name='本程序刺激物',dynamic=False))
            self.scene_count=1
        self._accepting_frames=True

    def _capture(self):
        if self.mode!='screen':return
        epoch=self._epoch;began=self.clock_ns()
        if self._backend is None:
            if self.backend_factory is None:raise RuntimeError('没有配置本地截屏后端；请通过悬浮层的注意力入口开始。')
            self._backend=self.backend_factory()
        if epoch!=self._epoch or not self.desired or self.user_paused or self.blocked:
            self._release_backend();return
        try:sample=self._backend.capture()
        except CapturePaused as exc:
            old=self._detector.last_old if self._detector is not None else None
            boundary=old.time_ns if old is not None and str(exc)=='foreground_or_filter_changed' else None
            self._state(str(exc),boundary);return
        if epoch!=self._epoch or not self.desired or self.user_paused or self.blocked:return
        self._state('recording',sample.time_ns)
        self._sample_count+=1
        if self._first_sample_ns is None:self._first_sample_ns=sample.time_ns
        self._last_sample_ns=sample.time_ns
        row=self._detector.observe(sample)
        self._store.append('scenes.jsonl',dict(type='screen_sample',time_ns=sample.time_ns,request_ns=sample.request_ns,received_ns=sample.received_ns,pending_since_ns=self._detector.pending_since,app_name=sample.app_name))
        if row:
            name='scenes/'+row['scene_id']+'.png';digest=self._store.write_image(name,sample.pixels)
            self._store.append('scenes.jsonl',dict(row,image=name,sha256=digest));self.scene_count+=1
        self.timings.append((self.clock_ns()-began)/1e6)
        if len(self.timings)>10000:self.timings=self.timings[-10000:]

    def _finish(self,reason):
        self._release_backend()
        if self._store is None:self.state='error' if self.error else 'ended';return
        end=self._stop_ns or self.clock_ns();self._pending_transition(end);self.state='finalizing';self._store.metadata['end_ns']=end
        self._store.append('scenes.jsonl',dict(type='state',time_ns=end,status=reason))
        if not self._cancel.is_set():
            self.summary=self.processor(self._store,self.settings.attention,cancelled=self._cancel.is_set)
            self.summary['capture_worker_ms']=dict(count=len(self.timings),median=float(np.median(self.timings)) if self.timings else None,p90=float(np.percentile(self.timings,90)) if self.timings else None)
            self.summary['observed_capture_hz']=((self._sample_count-1)*1e9/(self._last_sample_ns-self._first_sample_ns) if self._sample_count>1 and self._last_sample_ns>self._first_sample_ns else None)
            self.summary['configured_capture_hz']=self.settings.attention.capture_hz
            self.summary['processing_ms']=(self.clock_ns()-end)/1e6
            self._store.write_json('stats/session.json',self.summary)
        self._store.metadata['processing_complete']=self.summary is not None and not self.summary.get('warnings')
        self._store.metadata['processing_finished_ns']=self.clock_ns()
        self._store.finish(end,reason,self.error)
        finished=self.clock_ns()
        self._store.write_json('completion.json',dict(end_requested_ns=end,artifacts_flushed_ns=finished,finalization_ms=(finished-end)/1e6,definition='结束请求至主要数据文件写完并flush/fsync；不含本完成标记自身写入耗时'),final=True)
        self._store=None;self._metadata=None;self._detector=None
        self.state='error' if self.error else 'ended'

    def _run(self):
        next_capture=0.
        while not self._shutdown or self._store is not None or not self._commands.empty():
            command=None
            try:command=self._commands.get(timeout=.05 if self._store is not None else .5)
            except queue.Empty:pass
            try:
                if command:
                    kind=command[0]
                    if kind=='start':self._begin(command[1],command[2]);next_capture=0.
                    elif kind=='frame' and self._store is not None:
                        # Frames captured before pause remain valid; frames produced
                        # after it are prevented at observe and epoch fences capture.
                        self._store.append('gaze.jsonl',command[1])
                    elif kind=='state' and self._store is not None:self._state(command[1],command[2])
                    elif kind=='acceptance' and self._store is not None:
                        self._store.append('scenes.jsonl',command[1])
                    elif kind=='delete':
                        self.state='deleting';self._release_backend()
                        if self._store:self._store.close();self._store=None
                        delete_session(self.root,self.path);self.path=None;self.state='idle';self._end=None;self._metadata=None
                if self._store is not None and (self._end or not self.desired or self._shutdown) and self._commands.empty():
                    reason=self._end or 'stopped';self._end=None
                    if reason!='deleted':self._finish(reason)
                if self._store is not None and self.desired and not self.user_paused and not self.blocked and time.monotonic()>=next_capture:
                    next_capture=time.monotonic()+1/self.settings.attention.capture_hz
                    self._capture()
            except InterruptedError:
                if self._store:self._store.close();self._store=None
                self.state='deleting' if self._cancel.is_set() else 'ended'
            except Exception as exc:
                self.error=str(exc);self.desired=False;self._end=None;self._release_backend()
                if self._store:
                    try:
                        self._pending_transition(self.clock_ns())
                        self._store.finish(self.clock_ns(),'error',self.error)
                    except Exception:self._store.close()
                    self._store=None
                self.state='error'
        self._release_backend()
