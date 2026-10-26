"""Inference-thread owner of hands; only aggregate numbers reach loopback."""
from pathlib import Path
import threading
import time
from gaze_core.config import ROOT
from gaze_core.galaxy import HandControls,hand_features
from gaze_core.capture import timing_summary


class HandModel:
    def __init__(self,options):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        path=Path(options.hand_model)
        if not path.is_absolute():path=ROOT/path
        if not path.is_file():raise ValueError('缺少手势模型，请运行 setup_mvp.py --overlay；运行中不会下载')
        self.mp=mp;self.model=vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            # Avoid MediaPipe's native Windows path decoding; keep model I/O in Python.
            base_options=mp.tasks.BaseOptions(model_asset_buffer=path.read_bytes()),running_mode=vision.RunningMode.VIDEO,
            num_hands=2,min_hand_detection_confidence=options.hand_confidence,
            min_hand_presence_confidence=options.hand_confidence,min_tracking_confidence=options.hand_confidence))
        self.last_ms=-1

    def detect(self,image,capture_ns):
        import cv2
        stamp=capture_ns//1_000_000
        if stamp<=self.last_ms:return []
        self.last_ms=stamp
        rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        result=self.model.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=rgb),stamp)
        return [hand_features([[p.x,p.y,p.z] for p in hand]) for hand in result.hand_landmarks]

    def close(self):self.model.close()


class GalaxyProcessor:
    def __init__(self,bridge,options,radii,offset,*,model_factory=HandModel,clock_ns=time.perf_counter_ns):
        self.bridge,self.o,self.radii,self.offset=bridge,options,tuple(radii),tuple(offset)
        self.factory=model_factory;self.clock=clock_ns;self.model=None;self.controls=HandControls(options)
        self.origin=clock_ns();self.stopped=threading.Event()
        self.error=None;self.detected_frames=0;self.active_frames=0;self.hand_times=[];self.frames=0;self.first_ns=None;self.last_ns=None
        self.hand_error_code=0;self._last_sequence=-1;self._attempted=False

    def stop(self):self.stopped.set()

    def process(self,frame,data):
        if self.stopped.is_set():self.close();return
        mapping,revision,started=self.bridge.context()
        if mapping is None or started is None:return
        if frame.sequence<=self._last_sequence:return
        self._last_sequence=frame.sequence
        if not self._attempted:
            self._attempted=True
            try:self.model=self.factory(self.o)
            except Exception as exc:self.error='手势不可用，仅视线继续：'+str(exc);self.hand_error_code=1
        began=self.clock();features=[]
        try:
            if self.model is not None:features=self.model.detect(frame.image,frame.captured_ns)
        except Exception as exc:
            self.error='手势识别失败，仅视线继续：'+str(exc);self.hand_error_code=2;self.model.close();self.model=None;self.controls=HandControls(self.o)
        hand_ms=(self.clock()-began)/1e6
        self.hand_times.append(hand_ms)
        if len(self.hand_times)>36000:self.hand_times.pop(0)
        hands=self.controls.update(features,frame.captured_ns)
        self.detected_frames+=int(bool(features));self.active_frames+=hands['control']
        display=data.get('display',{});position=display.get('position')
        valid=bool(data.get('valid') and position is not None)
        xy=[0.,0.] if not valid else list(mapping.screen_to_window(tuple(p+d for p,d in zip(position,self.offset))))
        intro=int(frame.captured_ns<started+int(self.o.intro_s*1e9))
        scene=self.bridge.scene_feed(xy,self.radii,frame.captured_ns,valid and not intro,viewport_revision=revision)
        state={'tracking':0,'blink':1,'no_face':2}.get(data.get('status'),3)
        packet=dict(v=3,seq=int(frame.sequence),capture_us=max(0,(frame.captured_ns-self.origin)//1000),
            completed_us=max(0,(self.clock()-self.origin)//1000),viewport=revision,gaze_valid=int(valid),gaze_state=state,
            fixation={'fixation':1,'saccade':2}.get(display.get('state'),0),xy=xy,radius=list(self.radii),
            control=hands['control'],hands=hands['hands'],distance=hands['distance'],intro=intro,
            hand_ms=hand_ms,hand_available=int(self.model is not None),hand_detected=len(features[:2]),
            hand_openings=[float(f[2]) for f in features[:2]],hand_error=self.hand_error_code,**scene)
        if not self.stopped.is_set():self.bridge.publish(packet)
        self.frames+=1
        if self.first_ns is None:self.first_ns=frame.captured_ns
        self.last_ns=frame.captured_ns

    def report(self):
        elapsed=0 if self.first_ns is None else (self.last_ns-self.first_ns)/1e9
        return dict(frames=self.frames,capture_fps=(self.frames-1)/elapsed if elapsed else None,
                    hand_ms=timing_summary(self.hand_times),hand_error=self.error,
                    hand_detected_frames=self.detected_frames,hand_active_frames=self.active_frames)

    def close(self):
        if self.model is not None:self.model.close();self.model=None
