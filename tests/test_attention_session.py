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
PROFILE=dict(pipeline='python',source='nine_point',group='B',coordinate_version=2,conditions={},matrix=MATRIX,calibration_id='ninepoint-test')
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
            called=[];s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),backend_factory=lambda:called.append(True),processor=self.processor,root=Path(root)/'sessions')
            try:
                for profile in (None,dict(PROFILE,pipeline='web'),dict(PROFILE,kind='click_offset'),dict(PROFILE,conditions={'wrong':1}),dict(PROFILE,coordinate_version=1),dict(PROFILE,matrix=None)):
                    with self.assertRaises(ValueError):s.start(profile,{},KERNEL)
                self.assertFalse(called);self.assertFalse((Path(root)/'sessions').exists())
            finally:s.close()
    def test_fake_camera_frames_record_and_delete_no_resurrection(self):
        with tempfile.TemporaryDirectory() as root:
            counts=[]
            class Backend:
                def capture(self):
                    now=time.perf_counter_ns();counts.append('capture');return ScreenSample(np.zeros((200,320,3),np.uint8),now,now+1,'browser')
                def close(self):counts.append('close')
            s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),backend_factory=Backend,processor=self.processor,root=Path(root)/'sessions')
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
            s=AttentionSession(self.settings(Path(root)/'sessions'),ScreenRect(0,0,320,200),backend_factory=lambda:(_ for _ in ()).throw(AssertionError('must not screenshot')),processor=self.processor,root=Path(root)/'sessions')
            try:
                s.start(PROFILE,{},KERNEL,mode='stimulus',stimulus=dict(path=str(image),screen_rect=[.2,0,.8,1],kernel=KERNEL))
                self.wait(lambda:s.scene_count==1);s.stop();self.wait(lambda:s.state=='ended')
                metadata=json.loads((s.path/'session.json').read_text());self.assertEqual(metadata['stimulus']['sha256'],hashlib.sha256(image.read_bytes()).hexdigest())
            finally:s.close()
    def test_delete_during_finalization_stops_future_writes(self):
        with tempfile.TemporaryDirectory() as root:
            def blocked(store,options,cancelled):
                while not cancelled():time.sleep(.01)
                raise InterruptedError('cancelled')
            s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),processor=blocked,root=Path(root)/'sessions')
            image=Path(root)/'fixture.png';Image.new('RGB',(10,10)).save(image)
            try:
                s.start(PROFILE,{},KERNEL,mode='stimulus',stimulus=dict(path=str(image),screen_rect=[0,0,1,1],kernel=KERNEL))
                self.wait(lambda:s.scene_count==1);s.stop();self.wait(lambda:s.state=='finalizing');path=s.path
                s.delete();self.wait(lambda:s.path is None);self.assertFalse(path.exists())
            finally:s.close()
    def test_pause_while_backend_initializes_never_calls_capture(self):
        import threading
        entered=threading.Event();release=threading.Event();calls=[]
        class Backend:
            def capture(self):calls.append('capture');raise AssertionError('must not capture while paused')
            def close(self):calls.append('close')
        def factory():entered.set();release.wait(2);return Backend()
        with tempfile.TemporaryDirectory() as root:
            s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),backend_factory=factory,processor=self.processor,root=Path(root)/'sessions')
            try:
                s.start(PROFILE,{},KERNEL);self.assertTrue(entered.wait(2));s.pause();release.set();self.wait(lambda:s.state=='paused');self.assertNotIn('capture',calls)
            finally:release.set();s.close()
    def test_changed_camera_frame_rejected_before_ui_poll(self):
        with tempfile.TemporaryDirectory() as root:
            image=Path(root)/'fixture.png';Image.new('RGB',(10,10)).save(image)
            conditions=dict(camera=dict(width_px=640,height_px=480));profile=dict(PROFILE,conditions=conditions)
            s=AttentionSession(self.settings(root),ScreenRect(0,0,320,200),processor=self.processor,root=Path(root)/'sessions')
            try:
                s.start(profile,conditions,KERNEL,mode='stimulus',stimulus=dict(path=str(image),screen_rect=[0,0,1,1],kernel=KERNEL))
                self.wait(lambda:s.scene_count==1);now=time.perf_counter_ns()
                s.observe(NS(captured_ns=now,completed_ns=now+10,data=dict(valid=True,status='tracking',raw=[.5,.5],camera=dict(width_px=800,height_px=600))))
                self.wait(lambda:s.state=='paused');self.assertEqual(s.blocked,'camera_conditions_changed_restart_session')
                self.assertEqual(list(read_jsonl(s.path/'gaze.jsonl')),[])
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
                process_session(store,settings.attention,reocr=False,prefix='derived/test/')
                self.assertTrue((store.path/'derived/test/fixations.jsonl').exists());self.assertTrue((store.path/'derived/test/heatmaps/a.png').exists())
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
    def test_coregraphics_provider_padding_rows_ignored(self):
        from overlay.attention_macos import MacScreenCapture
        b=MacScreenCapture.__new__(MacScreenCapture)
        data=np.zeros((3,16),np.uint8);data[0,:4]=[2,3,4,255]
        b.Q=NS(CGImageGetWidth=lambda _:2,CGImageGetHeight=lambda _:2,CGImageGetBitsPerPixel=lambda _:32,
               CGImageGetBitmapInfo=lambda _:0x2002,CGImageGetBytesPerRow=lambda _:16,
               CGDataProviderCopyData=lambda _:data.tobytes(),CGImageGetDataProvider=lambda _:None)
        rgb=b._rgb(None);self.assertEqual(rgb.shape,(2,2,3));self.assertEqual(rgb[0,0].tolist(),[4,3,2])
    def test_permission_missing_never_starts_capture(self):
        from overlay.attention_macos import MacScreenCapture
        calls=[];q=NS(CGPreflightScreenCaptureAccess=lambda:False,CGRequestScreenCaptureAccess=lambda:calls.append('request'))
        with patch.dict('sys.modules',{'Quartz':q,'ScreenCaptureKit':NS(),'AppKit':NS(NSWorkspace=NS(sharedWorkspace=lambda:None))}):
            with self.assertRaisesRegex(PermissionError,'屏幕录制权限'):MacScreenCapture(ScreenRect(0,0,320,200))
            self.assertEqual(calls,[])
            with self.assertRaises(PermissionError):MacScreenCapture(ScreenRect(0,0,320,200),request_permission=True)
            self.assertEqual(calls,['request'])
    def test_activation_roundtrip_discards_even_same_frontmost(self):
        b=self.backend()
        def capture(f,c,cb):b._activation_revision=1;cb('image',None)
        b.S.SCScreenshotManager=NS(captureImageWithFilter_configuration_completionHandler_=capture)
        with patch.dict('sys.modules',{'objc':NS(autorelease_pool=nullcontext)}):
            with self.assertRaises(CapturePaused):b.capture()
    def test_excluded_foreground_never_calls_screenshot(self):
        b=self.backend();b._front=lambda:(123,'Secret','secret')
        with patch.dict('sys.modules',{'objc':NS(autorelease_pool=nullcontext)}):
            with self.assertRaisesRegex(CapturePaused,'excluded_app'):b.capture()
        self.assertIsNone(self.filtered)
    def test_front_change_discards_inflight_frame(self):
        b=self.backend();values=iter([(333,'Browser','browser'),(123,'Secret','secret')]);b._front=lambda:next(values)
        with patch.dict('sys.modules',{'objc':NS(autorelease_pool=nullcontext)}):
            with self.assertRaisesRegex(CapturePaused,'foreground_or_filter_changed'):b.capture()
