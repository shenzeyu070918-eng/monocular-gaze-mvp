from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
import numpy as np

from gaze_core.camera import CameraAdapter
from gaze_core.capture import Prediction
from gaze_core.config import load_settings
from gaze_core.profiles import build_conditions,save_click_profile,load_click_profile,click_profile_path
from overlay.autocalibration import AutocalManager
from overlay.click_hub import ClickHub
from overlay.mapping import ScreenRect
from test_autocalibration import stream


class AutocalProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.directory=Path(self.tmp.name)
        base=load_settings(use_local=False)
        self.settings=replace(base,geometry=replace(base.geometry,width_cm=30.,height_cm=20.,measured=True))
        self.screen=ScreenRect(0,0,1200,800)
        self.conditions=build_conditions(self.settings,CameraAdapter().prepare((640,480)).metadata,self.screen)
        self.now=0

    def test_minimal_numpy_profile_roundtrip_excludes_positions_and_detects_conditions(self):
        from overlay.profiles import conditions_id
        saved=save_click_profile([np.float64(.1),np.float32(-.2)],name='unit',count=np.int64(12),conditions=self.conditions,directory=self.directory)
        data=load_click_profile(name='unit',conditions=self.conditions,directory=self.directory)
        self.assertEqual(data,saved)
        self.assertEqual(data['conditions_fingerprint'],conditions_id(self.conditions))
        self.assertEqual(set(data),{'schema_version','pipeline','kind','offset_norm','sample_count','updated_at','conditions_fingerprint'})
        self.assertFalse((self.directory/'unit.json').exists())
        for mutate in (lambda c:c['camera'].update(index=9),lambda c:c['camera'].update(width_px=1280),
                       lambda c:c['screen'].update(width_cm=31.),lambda c:c.update(device_file='other.yaml',device_fingerprint='f'*64)):
            changed=deepcopy(self.conditions);mutate(changed)
            with self.assertRaisesRegex(ValueError,'不匹配'):load_click_profile(name='unit',conditions=changed,directory=self.directory)
        path=click_profile_path('unit',self.directory);raw=json.loads(path.read_text());raw['click_positions']=[[.1,.2]]
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError,'未允许'):load_click_profile(name='unit',conditions=self.conditions,directory=self.directory)

    def manager(self,**kwargs):
        value=AutocalManager(self.settings,name='unit',directory=self.directory,clock_ns=lambda:self.now,**kwargs)
        self.addCleanup(value.close);value.configure(self.conditions);self.fence(value);return value

    def fence(self,value):
        done=threading.Event();value._put(('barrier',done));self.assertTrue(done.wait(5))

    def test_default_off_loads_only_result_and_enabling_reuses_without_old_clicks(self):
        saved=save_click_profile([.1,-.2],name='unit',count=12,conditions=self.conditions,directory=self.directory)
        value=self.manager();self.assertFalse(value.enabled);self.assertEqual(value.profile,saved)
        value.set_enabled(True);self.fence(value)
        self.assertIsNone(value._learner);self.assertEqual(value.state()['used_count'],12)
        self.assertEqual(value.matrix,[[1.,0.],[0.,1.],[.1,-.2]])
        value.set_enabled(False);self.fence(value);self.assertFalse(value.enabled)
        self.assertEqual(value.profile,saved)

    def test_device_semantic_change_is_checked_by_same_validator_on_resume(self):
        save_click_profile([.1,-.2],name='unit',count=12,conditions=self.conditions,directory=self.directory)
        signature=[0];validate=Mock()
        value=self.manager(validate_device=validate,device_signature=lambda:signature[0])
        self.assertIsNotNone(value.profile)
        value.set_frozen(True);self.fence(value)
        signature[0]=1;validate.side_effect=ValueError('设备文件的标定/几何参数已改变')
        self.now=2_000_000_000;value.set_frozen(False);self.fence(value)
        self.assertIsNone(value.profile);self.assertIn('设备文件',value.reason)

    def test_learning_writes_only_offset_and_freeze_and_context_changes_discard_samples(self):
        value=self.manager();value.set_enabled(True);self.fence(value)
        frames,clicks=stream(12)
        for _,kind,row in sorted([(r['capture_ns'],0,r) for r in frames]+[(r['event_ns'],1,r) for r in clicks],key=lambda x:x[:2]):
            if kind==0:value.observe(Prediction(1,row['capture_ns'],row['capture_ns'],row))
            else:value.click(row)
        self.fence(value)
        self.assertIsNotNone(value.profile);self.assertEqual(value.profile['sample_count'],12)
        self.assertEqual(len(list(self.directory.iterdir())),1)
        self.assertNotIn('target',click_profile_path('unit',self.directory).read_text())
        identity=value.identity
        value.set_frozen(True);self.fence(value);value.click(clicks[-1]);self.fence(value)
        self.assertEqual(value.identity,identity)
        changed=deepcopy(self.conditions);changed['camera']['index']+=1
        value.configure(changed);self.fence(value)
        self.assertIsNone(value.profile);self.assertIsNone(value._learner)

    def test_freezing_discards_an_inflight_fit_before_profile_or_display_activation(self):
        from unittest.mock import patch
        from gaze_core.implicit_calibration import fit_robust
        value=self.manager();value.set_enabled(True);self.fence(value)
        entered=threading.Event();resume=threading.Event()
        def blocked(*args,**kwargs):
            entered.set();self.assertTrue(resume.wait(3));return fit_robust(*args,**kwargs)
        try:
            with patch('gaze_core.autocalibration.fit_robust',side_effect=blocked):
                frames,clicks=stream(10)
                for _,kind,row in sorted([(r['capture_ns'],0,r) for r in frames]+[(r['event_ns'],1,r) for r in clicks],key=lambda x:x[:2]):
                    if kind==0:value.observe(Prediction(1,row['capture_ns'],row['capture_ns'],row))
                    else:value.click(row)
                self.assertTrue(entered.wait(3));value.set_frozen(True);resume.set();self.fence(value)
            self.assertIsNone(value.profile);self.assertFalse(click_profile_path('unit',self.directory).exists())
        finally:resume.set()

    def test_C_events_record_and_replay_with_strict_numeric_whitelist(self):
        from gaze_core.config import RecordingSettings
        from gaze_core.recorder import SessionRecorder,validate_payload
        from gaze_core.evaluation import evaluate
        from tools.replay import read_session
        from test_recorder import metadata
        target=dict(target_id='unit',region='center',target=[.5,.5])
        summary=evaluate([dict(target_id='unit',valid=True,raw=[.4,.4],origin_m=[0,0,.5])],[target],self.settings.geometry,[[1,0],[0,1],[.02,.03]])
        summary['groups']['C']=summary['groups'].pop('B')
        fields=dict(task_id='check',run_id='run',calibration_id='click-unit',completed=True,reason='completed',summary=summary)
        recorder=SessionRecorder(metadata(),settings=RecordingSettings(str(self.directory),32,2))
        recorder.emit('calibration',action='applied',matrix=[[1,0],[0,1],[.02,.03]],calibration_id='click-unit',group='C',
                      coordinate_system='full_screen_normalized',error_radius_source='matching_C_check',error_radius_cm=2.)
        recorder.emit('evaluation_summary',**fields);recorder.close()
        events=read_session(recorder.path).records
        self.assertEqual(next(r for r in events if r['type']=='evaluation_summary')['summary'],summary)
        with self.assertRaisesRegex(ValueError,'网页'):validate_payload('evaluation_summary',dict(fields,browser_time_ms=1.),'web')
        with self.assertRaises(ValueError):validate_payload('evaluation_summary',dict(fields,image='base64'),'python')

    def test_shared_listener_ownership_and_recording_off_does_not_write(self):
        autocal=Mock(enabled=True,frozen=False,conditions=self.conditions)
        recording=Mock();recording.status.return_value=dict(active=False)
        listener=Mock();listener.start.return_value=True;listener.status.return_value=dict(active=True,error=None)
        hub=ClickHub(self.settings,self.screen,recording,autocal,listener_factory=lambda *a,**k:listener)
        hub.sync();listener.start.assert_called_once()
        self.assertTrue(hub._emit('click',event_ns=100,x=.2,y=.3))
        autocal.click.assert_called_once();recording.emit.assert_not_called();recording.acquire.assert_not_called()
        recording.status.return_value=dict(active=True)
        hub.recording_listener.start();hub._emit('click',event_ns=101,x=.2,y=.3)
        recording.emit.assert_called_once();listener.start.assert_called_once()
        hub.recording_listener.stop();listener.stop.assert_not_called()
        autocal.enabled=False;hub.sync();listener.stop.assert_called_once()
        hub.close()


if __name__=='__main__':unittest.main()
