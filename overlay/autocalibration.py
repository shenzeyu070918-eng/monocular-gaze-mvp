"""Background M5 sampling/profile I/O, independent of Qt and native input."""
from copy import deepcopy
import queue
import threading
import time

from gaze_core.autocalibration import OnlineOffset,correction_matrix
from gaze_core.config import ROOT
from gaze_core.implicit_calibration import load_experiment_settings,cm_scale
from gaze_core.profiles import (click_profile_path,conditions_fingerprint,load_click_profile,save_click_profile,_fingerprint)


class AutocalManager:
    def __init__(self,settings,*,name,directory=None,clock_ns=time.perf_counter_ns,validate_device=None,device_signature=None):
        self.settings,self.options,self.name=settings,settings.autocal,name
        self.directory=directory;self.clock_ns=clock_ns
        self.validate_device=validate_device or (lambda:None)
        self.device_signature=device_signature or (lambda:None)
        self.enabled=self.options.enabled;self.frozen=False;self.closed=False
        self.conditions=None;self._profile_state=(None,None);self.error=None;self.reason='点击自动粗校准：关闭'
        self.revision=0;self._epoch=0;self._learner=None;self._check=None
        self._state=dict(seen=0,accepted=0,rejected=0,pending=0,used_count=0,reasons={},fit_error=None)
        self._queue=queue.Queue(self.options.queue_size);self._stop=threading.Event()
        self._next_poll=0.;self._stamp=object();self._published_revision=0
        self.thread=threading.Thread(target=self._run,name='gaze-autocal',daemon=True);self.thread.start()

    def _put(self,command):
        if self.closed:return False
        try:self._queue.put_nowait(command);return True
        except queue.Full:
            self.enabled=False;self.error='自动粗校准队列已满，已停止学习；显示和推理继续。';self.revision+=1;return False

    def configure(self,conditions):
        if self.conditions==conditions:return
        if self.enabled:
            try:cm_scale(self.settings.geometry)
            except ValueError:self.enabled=False;self.error='点击自动粗校准需要当前设备的实测屏幕宽高，请先补齐设备配置。'
        self._epoch+=1;self.conditions=deepcopy(conditions);self._activate(None);self._reset_state();self.revision+=1
        self._put(('configure',self._epoch,deepcopy(conditions)))

    def set_enabled(self,enabled):
        if enabled:
            try:cm_scale(self.settings.geometry)
            except ValueError as exc:raise ValueError('点击自动粗校准需要当前设备的实测屏幕宽高，请先补齐设备配置。') from exc
            if self.conditions is None:raise ValueError('请等待实际相机条件就绪')
        self.enabled=bool(enabled);self.error=None;self._reset_state();self._epoch+=1;self.revision+=1
        self._put(('enable',self._epoch,self.enabled))
        return True

    def _reset_state(self):
        self._state=dict(seen=0,accepted=0,rejected=0,pending=0,used_count=0,reasons={},fit_error=None)

    def set_frozen(self,frozen):
        frozen=bool(frozen)
        if self.frozen==frozen:return
        self.frozen=frozen;self._epoch+=1
        self._put(('interrupt',self._epoch))

    def observe(self,prediction):
        if not self.enabled or self.frozen or self.conditions is None or self.closed:return
        data=prediction.data
        frame={key:deepcopy(data.get(key)) for key in ('raw','valid','status','origin_m','head_degrees')}
        frame['capture_ns']=int(prediction.captured_ns)
        self._put(('frame',self._epoch,frame))

    def click(self,fields):
        if self.enabled and not self.frozen and self.conditions is not None:
            self._put(('click',self._epoch,{key:fields.get(key) for key in ('event_ns','x','y','excluded','exclusion_reason')}))
        return True

    @property
    def profile(self):return self._profile_state[0]

    @property
    def identity(self):return self._profile_state[1]

    def _activate(self,profile):
        identity=None if profile is None else 'click-'+_fingerprint([profile['conditions_fingerprint'],profile['offset_norm']])
        if identity!=self.identity:self._check=None
        self._profile_state=(profile,identity)

    def candidate(self):
        profile,identity=self._profile_state
        radius=self._check[1] if self._check is not None and self._check[0]==identity else None
        return profile,identity,None if profile is None else correction_matrix(profile['offset_norm']),radius

    @property
    def matrix(self):return None if self.profile is None else correction_matrix(self.profile['offset_norm'])

    @property
    def radius(self):
        return self._check[1] if self._check is not None and self._check[0]==self.identity else None

    def save_check(self,result):
        if self.profile is None or result['calibration_id']!=self.identity:return False
        group=result['summary']['groups'].get('C',{}).get('all',{})
        mean=group.get('mean_cm')
        if not result['summary']['measured'] or not group.get('complete') or mean is None or mean<=0:return False
        self._check=(self.identity,float(mean));self.revision+=1;return True

    def state(self):
        profile=self.profile;count=0 if profile is None else profile['sample_count']
        offset=None if profile is None else profile['offset_norm']
        scale=cm_scale(self.settings.geometry) if self.settings.geometry.measured else None
        return dict(self._state,enabled=self.enabled,error=self.error,reason=self.reason,profile=profile,used_count=count,
                    offset_cm=None if offset is None or scale is None else [float(v*s) for v,s in zip(offset,scale)])

    def _new_learner(self):
        from pathlib import Path
        path=Path(self.options.sampling_config)
        if not path.is_absolute():path=ROOT/'config'/path
        return OnlineOffset(self.settings.geometry,load_experiment_settings(path),self.options)

    def _file_stamp(self):
        try:
            stat=click_profile_path(self.name,self.directory).stat()
            own=(stat.st_ino,stat.st_size,stat.st_mtime_ns)
        except FileNotFoundError:own=None
        return own,self.device_signature()

    def _poll_profile(self):
        if self.conditions is None or self.frozen or self.clock_ns()<self._next_poll:return
        self._next_poll=self.clock_ns()+round(self.settings.overlay.profile_poll_s*1e9)
        stamp=self._file_stamp()
        if stamp==self._stamp:return
        epoch=self._epoch;conditions=deepcopy(self.conditions)
        try:
            self.validate_device()
            profile=load_click_profile(name=self.name,conditions=conditions,directory=self.directory) if stamp[0] else None
            if profile is not None and profile['sample_count']<self.options.min_clicks:
                raise ValueError('粗校准档案的点击数未达到当前最少点击数')
            self.reason='已匹配点击粗校准档案' if profile is not None else '没有匹配粗校准档案，等待新点击'
            if profile is not None and profile.get('coordinate_version',1)==1:
                self.reason+='（旧版原生全局坐标，不受任务窗口偏移影响）'
        except (ValueError,OSError) as exc:
            profile=None;self.reason=str(exc)
        if epoch==self._epoch:
            self._stamp=stamp;self._activate(profile);self.revision+=1

    def _run(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:command=self._queue.get(timeout=.1)
            except queue.Empty:command=None
            try:
                if command is not None:
                    kind=command[0]
                    if kind=='barrier':command[1].set();continue
                    if kind=='configure':
                        self._learner=None;self._published_revision=0;self._stamp=object();self._next_poll=0
                    elif kind in {'enable','interrupt'}:
                        # Rebuild the signal history, retaining only the bounded
                        # accepted samples during a task/pause. Never bridge gaps.
                        previous=self._learner
                        self._learner=None
                        if kind=='interrupt' and previous is not None and self.enabled:
                            self._learner=self._new_learner();self._learner.samples.extend(previous.samples)
                        self._published_revision=0
                    elif kind in {'frame','click'} and command[1]==self._epoch and self.enabled and not self.frozen:
                        epoch=command[1]
                        if self._learner is None:self._learner=self._new_learner()
                        getattr(self._learner,kind)(command[2])
                        if epoch!=self._epoch:continue  # A task/condition change invalidates in-flight fits.
                        state=self._learner.state()
                        if state['revision']!=self._published_revision and state['matrix'] is not None:
                            self._published_revision=state['revision']
                            conditions=deepcopy(self.conditions)
                            if not self.frozen and self.enabled:
                                self.validate_device()
                                profile=save_click_profile(state['offset_norm'],name=self.name,count=state['used_count'],
                                                           conditions=conditions,directory=self.directory)
                                if epoch==self._epoch and not self.frozen and self.enabled:
                                    self._activate(profile);self._stamp=self._file_stamp();self.revision+=1
                        if epoch==self._epoch and self._state!=state:self._state=state;self.revision+=1
                if self._learner is not None and self.enabled and not self.frozen:
                    self._learner.advance(self.clock_ns())
                if not self.enabled:self._learner=None
                self._poll_profile()
            except Exception as exc:
                self.error='自动粗校准失败：'+str(exc);self.enabled=False;self._learner=None;self.revision+=1

    def close(self):
        self.enabled=False;self.closed=True;self._epoch+=1;self._stop.set()
        self.thread.join(self.options.close_timeout_s)
        if self.thread.is_alive():raise RuntimeError('自动粗校准后台线程尚未退出')
        self._learner=None
