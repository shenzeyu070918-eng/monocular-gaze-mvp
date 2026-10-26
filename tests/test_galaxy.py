"""Synthetic controls only; no camera, real browser or user inputs."""
from dataclasses import replace
import unittest
from unittest.mock import Mock
import numpy as np
from gaze_core.galaxy import GalaxySettings,HandControls,PatternCycle,hand_features
from gaze_core.galaxy_protocol import viewport,validate_frame,telemetry
from gaze_core.demo_frame import DemoFrameSlot
from gaze_core.capture import CapturedFrame
from overlay.mapping import ScreenRect
from overlay.galaxy_processor import GalaxyProcessor
from overlay.galaxy_bridge import GalaxyBridge


def geometry(**changes):
    d=dict(screenX=0,screenY=0,innerWidth=1512,innerHeight=950,outerWidth=1512,outerHeight=982,dpr=2,
        screenWidth=1512,screenHeight=982,fullscreen=1,clientX=100,clientY=100,eventScreenX=100,eventScreenY=132,scale=1)
    d.update(changes);return d


def processor_bridge(options,mapping,*,started=0,clock=lambda:0):
    bridge=GalaxyBridge(options,ScreenRect(0,0,1512,982,2),clock_ns=clock)
    bridge.mapping=mapping;bridge.revision=1;bridge.started_ns=started;bridge.publish=Mock()
    return bridge


class GalaxyTests(unittest.TestCase):
    def test_options_reject_bad_values_and_accept_unicode_text(self):
        self.assertEqual(GalaxySettings().text,'BME')
        self.assertEqual(replace(GalaxySettings(),text='眼手星系').text,'眼手星系')
        for change in (dict(text=''),dict(text='a\nb'),dict(particle_count=2000),dict(pixel_ratio=np.nan),dict(hand_enter_frames=True),dict(hand_closed=.9)):
            with self.assertRaises(ValueError):replace(GalaxySettings(),**change)

    def test_notch_viewport_uses_click_client_origin_not_screenY(self):
        m=viewport(ScreenRect(0,0,1512,982,2),geometry())
        self.assertTrue(m.ready);self.assertEqual(m.window_origin,(0.,32.))
        self.assertEqual(m.screen_to_window((.5,.5)),(755.5,458.5))
        for change in (dict(scale=1.2),dict(dpr=2.2),dict(fullscreen=0),dict(eventScreenY=500),dict(screenHeight=950),dict(image='forbidden')):
            with self.assertRaises(ValueError):viewport(ScreenRect(0,0,1512,982,2),geometry(**change))
        scaled=viewport(ScreenRect(0,0,1512,982,1.25),geometry(dpr=1.25))
        self.assertEqual(scaled.screen_to_window((.5,.5)),m.screen_to_window((.5,.5)))

    def test_hand_three_distinct_frames_and_timeout(self):
        h=HandControls(GalaxySettings())
        for i in range(2):self.assertEqual(h.update([[.5,.5,1.,.1]],i*70_000_000)['control'],0)
        self.assertEqual(h.update([[.5,.5,1.,.1]],140_000_000)['control'],1)
        self.assertEqual(h.update([],600_000_000)['control'],1)
        self.assertEqual(h.update([],641_000_000)['control'],0)
        for i in range(5):h.update([[.5,.5,1.,.1]],642_000_000)
        self.assertFalse(h.active)  # Duplicate timestamp never adds a frame.

    def test_hand_continuity_hysteresis_speed_and_pair_association(self):
        h=HandControls(replace(GalaxySettings(),hand_smoothing_ms=1))
        for i in range(3):r=h.update([[.2,.5,.1,.1],[.8,.5,1.,.2]],i*100_000_000)
        self.assertEqual(r['hands'][0][6],-1);self.assertEqual(r['hands'][1][6],1)
        self.assertAlmostEqual(r['distance'],.6)
        for i in range(3,8):r=h.update([[.79,.5,.5,.1],[.21,.5,.5,.1]],i*100_000_000)
        self.assertEqual([p[6] for p in r['hands']],[1,-1])
        self.assertTrue(all(np.isfinite(p).all() for p in r['hands']))

    def test_hand_feature_mirror_scale_and_no_landmarks_in_output(self):
        p=np.zeros((21,3));p[:,0]=np.linspace(.2,.7,21);p[:,1]=np.arange(21)%4*.03
        a=hand_features(p);b=hand_features(p*2)
        self.assertEqual(len(a),4);self.assertAlmostEqual(a[2],b[2]);self.assertAlmostEqual(a[3],b[3])
        self.assertAlmostEqual(a[0],1-p[[0,5,9,13,17],0].mean())
        with self.assertRaises(ValueError):hand_features(np.zeros((21,3)))

    def test_pattern_capture_time_and_transition_rearm(self):
        cycle=PatternCycle(GalaxySettings())
        for ms in range(0,1501,100):self.assertEqual(cycle.feed([0,0],[10,10],ms*1_000_000,viewport_size=[1000,800]),int(ms==1500))
        for ms in range(1600,2700,100):self.assertEqual(cycle.feed([0,0],[10,10],ms*1_000_000,viewport_size=[1000,800]),1)
        self.assertEqual(cycle.feed([0,0],[10,10],2_600_000_000,viewport_size=[1000,800]),1)
        cycle.complete(cycle.revision,3_700_000_000)
        cycle.feed([0,0],[10,10],3_800_000_000,False);cycle.feed([0,0],[10,10],4_101_000_000,False);self.assertEqual(cycle.snapshot()['corner'],-1)

    def test_pattern_only_corners_capture_gap_and_three_scenes(self):
        cycle=PatternCycle(GalaxySettings())
        for ms in range(0,3001,100):cycle.feed([500,400],[30,30],ms*1_000_000,viewport_size=[1000,800])
        self.assertEqual(cycle.pattern,0)
        for ms in range(3100,4601,100):cycle.feed([20,20],[30,30],ms*1_000_000,viewport_size=[1000,800])
        self.assertEqual(cycle.pattern,1)
        cycle.complete(cycle.revision,6_800_000_000)
        for ms in range(6900,8501,100):cycle.feed([980,20],[30,30],ms*1_000_000,viewport_size=[1000,800])
        self.assertEqual(cycle.pattern,2)
        cycle.complete(cycle.revision,10_600_000_000)
        for ms in range(10700,12301,100):cycle.feed([980,780],[30,30],ms*1_000_000,viewport_size=[1000,800])
        self.assertEqual(cycle.pattern,0)
        cycle.complete(cycle.revision,14_500_000_000)
        cycle.feed([20,780],[30,30],14_600_000_000,viewport_size=[1000,800])
        cycle.feed([20,780],[30,30],16_600_000_000,viewport_size=[1000,800])
        self.assertEqual(cycle.pattern,0)  # A capture gap is not dwell evidence.
        cycle.feed([-300,780],[30,30],16_700_000_000,viewport_size=[1000,800])
        cycle.feed([-300,780],[30,30],17_001_000_000,viewport_size=[1000,800])
        self.assertEqual(cycle.snapshot()['corner'],-1)

    def test_processor_same_frame_no_raw_mutation_intro_and_numeric_whitelist(self):
        options=GalaxySettings();screen=ScreenRect(0,0,1512,982,2);mapping=viewport(screen,geometry())
        now=[10_000_000_000];bridge=processor_bridge(options,mapping,started=now[0],clock=lambda:now[0])
        model=Mock();model.detect.return_value=[[.5,.5,1.,.2]]
        processor=GalaxyProcessor(bridge,options,[50,50],[.01,-.02],model_factory=lambda _:model,clock_ns=lambda:now[0])
        image=np.zeros((3,3,3),np.uint8);data=dict(valid=True,status='tracking',raw=[.8,.2],display=dict(position=[.5,.5],state='fixation'))
        for i in range(4):
            now[0]+=100_000_000;processor.process(CapturedFrame(i,now[0],image),data)
        packet=bridge.publish.call_args.args[0];validate_frame(packet)
        self.assertEqual(packet['intro'],1);self.assertEqual(packet['pattern'],0)
        self.assertEqual(packet['v'],4);self.assertEqual(packet['hand_detected'],1);self.assertEqual(packet['hand_openings'],[1.]);self.assertEqual(packet['hand_error'],0)
        self.assertEqual(packet['control'],1);self.assertEqual(data['raw'],[.8,.2])
        self.assertIs(model.detect.call_args.args[0],image)
        self.assertAlmostEqual(packet['xy'][1],.48*981-32)
        for bad in (dict(image='data:image'),dict(raw=[.1,.2]),dict(xy=[np.inf,0]),dict(hands=[[0,0,'image']]),dict(intro=3),dict(hand_available=2),dict(distance=[]),dict(radius=1),dict(hand_detected=3),dict(hand_openings=[np.nan]),dict(hand_error=3),dict(hand_openings=[.1,.2]),dict(pattern=3),dict(v=2),dict(corner=4),dict(corner_progress=1.1),dict(corner_dwell_ms=-1),dict(corner_progress=[]),dict(scene_revision=-1),dict(corner_reset=5),dict(scene_from=3),dict(scene_transition=2),dict(scene_transition_ms=-1),dict(burst_active=2),dict(burst_active=1),dict(scene_transition=1),dict(blink_reason=8),dict(burst_elapsed_ms=[]),dict(blink_closed_ms=float("nan"))):
            with self.assertRaises(ValueError):validate_frame(dict(packet,**bad))
        processor.stop();processor.process(CapturedFrame(9,now[0],image),data);model.close.assert_called_once()

    def test_processor_diagnostics_before_takeover_missing_and_failed_model(self):
        for fault,expected in (('none',0),('load',1),('detect',2)):
            with self.subTest(fault=fault):
                bridge=processor_bridge(GalaxySettings(),viewport(ScreenRect(0,0,1512,982,2),geometry()))
                model=Mock();model.detect.return_value=[[.5,.5,np.float64(.8),.2]]
                if fault=='detect':model.detect.side_effect=ValueError('synthetic detection failure')
                def factory(_):
                    if fault=='load':raise ValueError('synthetic missing model')
                    return model
                processor=GalaxyProcessor(bridge,GalaxySettings(),[50,50],[0,0],model_factory=factory,clock_ns=lambda:3_000_000_000)
                try:
                    processor.process(CapturedFrame(1,3_000_000_000,np.zeros((3,3,3),np.uint8)),dict(valid=False,status='no_face'))
                    packet=validate_frame(bridge.publish.call_args.args[0])
                    self.assertEqual(packet['hand_error'],expected);self.assertEqual(packet['control'],0)
                    self.assertEqual(packet['hand_detected'],1 if fault=='none' else 0)
                    self.assertEqual(packet['hand_available'],int(fault=='none'))
                    self.assertEqual(processor.report()['hand_detected_frames'],int(fault=='none'))
                    self.assertFalse(packet['gaze_valid'])  # Hand detection runs even without a face.
                finally:processor.close()
                if fault!='load':model.close.assert_called_once()

    def test_processor_corner_uses_local_viewport(self):
        options=replace(GalaxySettings(),dwell_ms=300)
        mapping=viewport(ScreenRect(0,0,1512,982,2),geometry());bridge=processor_bridge(options,mapping)
        model=Mock();model.detect.return_value=[]
        processor=GalaxyProcessor(bridge,options,[50,50],[0,0],model_factory=lambda _:model,clock_ns=lambda:5_000_000_000)
        image=np.zeros((3,3,3),np.uint8)
        # y=.06 maps to 26.86 in the y=32 notch client; still the upper-left corner.
        data=dict(valid=True,status='tracking',display=dict(position=[.05,.06],state='fixation'))
        try:
            for i in range(5):processor.process(CapturedFrame(i,3_000_000_000+i*100_000_000,image),data)
            self.assertEqual(bridge.publish.call_args.args[0]['pattern'],1)
        finally:processor.close()

    def test_processor_real_blink_status_drives_capture_clock_burst(self):
        options=GalaxySettings();mapping=viewport(ScreenRect(0,0,1512,982,2),geometry())
        for bad_eye in (False,True):
            bridge=processor_bridge(options,mapping);model=Mock();model.detect.return_value=[]
            processor=GalaxyProcessor(bridge,options,[50,50],[0,0],model_factory=lambda _:model,clock_ns=lambda:5_000_000_000)
            image=np.zeros((3,3,3),np.uint8)
            for n,ms in enumerate(range(3000,3801,100)):
                status='blink' if 3100<=ms<=3700 else 'tracking'
                data=dict(status=status,valid=status=='tracking',display=dict(position=[.5,.5]))
                if bad_eye and ms==3800:data['eye_openness']=float('nan')
                processor.process(CapturedFrame(n,ms*1_000_000,image),data)
            packet=validate_frame(bridge.publish.call_args.args[0])
            self.assertEqual(packet['blink_count'],0 if bad_eye else 1)
            self.assertEqual(packet['burst_source'],0 if bad_eye else 1)
            self.assertNotIn('eye_openness',packet);self.assertNotIn('image',packet)
            processor.close()

    def test_slot_switches_and_closes_only_on_processing_thread(self):
        slot=DemoFrameSlot();a=Mock();b=Mock();slot.set(a);a.process.assert_not_called()
        slot.process(None,{});a.process.assert_called_once()
        slot.set(b);a.close.assert_not_called();slot.process(None,{});a.close.assert_called_once()
        slot.set(None);slot.process(None,{});b.close.assert_called_once()

    def test_telemetry_rejects_text_and_unknown_fields(self):
        row=dict(fps=60,p90_ms=16.7,frames=60,long_frames=0,particles=50000,pixel_ratio=1,downgrades=0,render_median_ms=.2,render_p90_ms=.3)
        self.assertEqual(telemetry(row)['fps'],60)
        self.assertEqual(telemetry(dict(row,particles=230000))['particles'],230000)
        for bad in ({'particles':230000.5},{'render_p90_ms':np.inf},{'render_median_ms':-1},{'title':'private'}):
            with self.assertRaises(ValueError):telemetry(dict(row,**bad))


if __name__=='__main__':unittest.main()
