from dataclasses import asdict,replace
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import nullcontext
from unittest.mock import patch
import hashlib,json,os,tempfile,time,unittest
import numpy as np
from PIL import Image
from gaze_core.config import load_settings,ScreenGeometry
from gaze_core.attention.session import AttentionSession
from gaze_core.attention.storage import SessionStore,read_jsonl
from gaze_core.attention.processing import process_session
from gaze_core.attention.contracts import ScreenSample,CapturePaused
from overlay.mapping import ScreenRect

MATRIX=[[1,0],[0,1],[0,0]]
PROFILE=dict(pipeline='python',conditions={},matrix=MATRIX,calibration_id='ninepoint-test')
KERNEL=dict(sigma_x=.06,sigma_y=.1,sigma_cm=2.,sigma_px=None,source='configured_cm')

class AttentionSessionTests(unittest.TestCase):
    def settings(self,root):
        base=load_settings(use_local=False)
        return replace(base,attention=replace(base.attention,output_dir=str(root),capture_hz=5,stable_ms=1,min_scene_interval_ms=1),geometry=ScreenGeometry(30,20,0,1,measured=True))
    def wait(self,condition):
        end=time.monotonic()+5
        while time.monotonic()<end:
            if condition():return
            time.sleep(.01)
        self.fail('worker timeout')
    def processor(self,store,options,**kwargs):return dict(warnings=[],duration_s=1.)
    def test_require_matching_ninepoint_and_default_no_capture(self):
        with tempfile.TemporaryDirectory() as root:
            called=[];s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),backend_factory=lambda:called.append(True),processor=self.processor)
            try:
                for profile in (None,dict(PROFILE,pipeline='web'),dict(PROFILE,kind='click_offset'),dict(PROFILE,conditions={'wrong':1})):
                    with self.assertRaises(ValueError):s.start(profile,{},KERNEL)
                self.assertFalse(called);self.assertEqual(list(Path(root).iterdir()),[])
            finally:s.close()
    def test_fake_camera_frames_record_and_delete_no_resurrection(self):
        with tempfile.TemporaryDirectory() as root:
            counts=[]
            class Backend:
                def capture(self):
                    now=time.perf_counter_ns();counts.append('capture');return ScreenSample(np.zeros((200,320,3),np.uint8),now,now+1,'browser')
                def close(self):counts.append('close')
            s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),backend_factory=Backend,processor=self.processor)
            try:
                s.start(PROFILE,{},KERNEL);self.wait(lambda:s.scene_count==1)
                for i in range(3):
                    now=time.perf_counter_ns();s.observe(NS(captured_ns=now,completed_ns=now+10,data=dict(valid=True,status='tracking',raw=[np.float64(.5),.5])))
                s.pause();self.wait(lambda:s.state=='paused');count=counts.count('capture');time.sleep(.3);self.assertEqual(counts.count('capture'),count)
                s.pause();self.wait(lambda:counts.count('capture')>count)
                s.stop();self.wait(lambda:s.state in ('ended','error'));self.assertIsNone(s.error)
                path=s.path;self.assertTrue(any(r['type']=='end' for r in read_jsonl(path/'scenes.jsonl')))
                s.delete();self.wait(lambda:s.path is None);time.sleep(.25);self.assertFalse(path.exists())
            finally:s.close()
    def test_stimulus_never_constructs_capture_and_has_source_hash(self):
        with tempfile.TemporaryDirectory() as root:
            image=Path(root)/'中文 图像.png';Image.new('RGB',(100,100),'white').save(image)
            s=AttentionSession(self.settings(Path(root)/'sessions'),ScreenRect(0,0,320,200),backend_factory=lambda:(_ for _ in ()).throw(AssertionError('must not screenshot')),processor=self.processor)
            try:
                s.start(PROFILE,{},KERNEL,mode='stimulus',stimulus=dict(path=str(image),screen_rect=[.2,0,.8,1],kernel=KERNEL))
                self.wait(lambda:s.scene_count==1);s.stop();self.wait(lambda:s.state=='ended')
                metadata=json.loads((s.path/'session.json').read_text());self.assertEqual(metadata['stimulus']['sha256'],hashlib.sha256(image.read_bytes()).hexdigest())
            finally:s.close()
    def test_real_processing_roundtrip_numpy_no_ocr_cloud(self):
        with tempfile.TemporaryDirectory() as root:
            settings=self.settings(root);metadata=dict(start_ns=0,end_ns=1000000000,geometry=asdict(settings.geometry),fixation=asdict(settings.fixation),heatmap=asdict(settings.heatmap),kernel=KERNEL,profile=PROFILE,mode='screen',analysis_size=[320,200])
            store=SessionStore(root,metadata,10_000_000)
            try:
                sha=store.write_image('scenes/a.png',np.zeros((200,320,3),np.uint8));store.append('scenes.jsonl',dict(type='scene',scene_id='a',start_ns=0,image='scenes/a.png',sha256=sha))
                for i in range(10):store.append('gaze.jsonl',dict(capture_ns=i*100000000,raw=[np.float64(.25),.5],valid=True,status='tracking'))
                summary=process_session(store,settings.attention,ocr_function=lambda *_:[])
                self.assertAlmostEqual(summary['valid_bound_fixation_s'],.9);self.assertTrue((store.path/'heatmaps/a.png').exists())
                rows=list(read_jsonl(store.path/'fixations.jsonl'));self.assertEqual(rows[0]['center'],[.25,.5])
                self.assertEqual(rows[0]['end_ns'],900000000)
            finally:store.close()

class NativeAdapterTests(unittest.TestCase):
    def backend(self):
        from overlay.attention_macos import MacScreenCapture
        own=NS(processID=lambda:os.getpid(),applicationName=lambda:'Python',bundleIdentifier=lambda:'own')
        other=NS(processID=lambda:123,applicationName=lambda:'Secret',bundleIdentifier=lambda:'secret')
        display=NS(displayID=lambda:1,frame=lambda:NS(size=NS(width=320,height=200)))
        content=NS(applications=lambda:[own,other],displays=lambda:[display])
        self.config=NS();self.filtered=None
        for key in ('Width','Height','ShowsCursor','CapturesAudio'):setattr(self.config,'set'+key+'_',lambda value,k=key:setattr(self.config,k,value))
        config_type=NS(alloc=lambda:NS(init=lambda:self.config))
        def filter_init(display,apps,exceptions):self.filtered=apps;return 'filter'
        filters=NS(alloc=lambda:NS(initWithDisplay_excludingApplications_exceptingWindows_=filter_init))
        capture=NS(captureImageWithFilter_configuration_completionHandler_=lambda f,c,cb:cb('image',None))
        b=MacScreenCapture.__new__(MacScreenCapture);b.Q=NS(CGDisplayIsBuiltin=lambda n:1);b.S=NS(SCContentFilter=filters,SCStreamConfiguration=config_type,SCScreenshotManager=capture)
        b.screen=ScreenRect(0,0,320,200);b.excluded={'Secret'};b.closed=False;b._content=lambda:content;b._front=lambda:(333,'Browser','browser')
        b._rgb=lambda image:np.zeros((200,320,3),np.uint8)
        return b
    def test_cursor_disabled_and_all_own_app_windows_excluded(self):
        b=self.backend()
        with patch.dict('sys.modules',{'objc':NS(autorelease_pool=nullcontext)}):sample=b.capture()
        self.assertFalse(self.config.ShowsCursor);self.assertFalse(self.config.CapturesAudio)
        self.assertEqual(len(self.filtered),2);self.assertEqual(sample.pixels.shape,(200,320,3))
    def test_excluded_foreground_never_calls_screenshot(self):
        b=self.backend();b._front=lambda:(123,'Secret','secret')
        with patch.dict('sys.modules',{'objc':NS(autorelease_pool=nullcontext)}):
            with self.assertRaisesRegex(CapturePaused,'excluded_app'):b.capture()
        self.assertIsNone(self.filtered)
    def test_front_change_discards_inflight_frame(self):
        b=self.backend();values=iter([(333,'Browser','browser'),(123,'Secret','secret')]);b._front=lambda:next(values)
        with patch.dict('sys.modules',{'objc':NS(autorelease_pool=nullcontext)}):
            with self.assertRaisesRegex(CapturePaused,'foreground_or_filter_changed'):b.capture()
