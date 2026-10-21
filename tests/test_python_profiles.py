"""No hardware: numeric profiles, chain isolation and exact runtime conditions."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from gaze_core.camera import CameraAdapter
from gaze_core.config import load_settings
from gaze_core.devices import parse_device
from gaze_core.evaluation import evaluate
from gaze_core.profiles import (build_conditions, condition_mismatch_reason, device_fingerprint,
                                load_manual_web_profile, load_profile, load_python_profile,
                                profile_age_text, radius_from_profile, save_profile,
                                save_python_check, save_python_profile)
from overlay.mapping import ScreenRect


IDENTITY = [[np.float64(1), 0], [0, 1], [0, 0]]


def synthetic_device():
    return dict(schema_version=1,name='synthetic',
                camera=dict(matrix=[[610.,0.,320.],[0.,610.,240.],[0.,0.,1.]],distortion=[.01,0.,0.,0.,0.],
                            resolution=[640,480],reprojection_error_px=.2,calibrated_at='2026-10-20T00:00:00+00:00'),
                screen=dict(width_cm=30.,height_cm=20.,measured=True,resolution=[1512,982]),
                camera_offset_cm=dict(horizontal=0.,vertical=.3))


class PythonProfilesTests(unittest.TestCase):
    def setUp(self):
        base=load_settings(use_local=False)
        self.device=parse_device(synthetic_device(),'synthetic.yaml')
        self.settings=replace(base,device_profile=self.device,device_file='devices/synthetic.yaml',
                              geometry=replace(base.geometry,width_cm=30.,height_cm=20.,measured=True,top_cm=.3),
                              camera=replace(base.camera,index=1))
        self.screen=ScreenRect(0,0,1512,982,2.)
        self.metadata=CameraAdapter(self.device).prepare((640,480)).metadata
        self.conditions=build_conditions(self.settings,self.metadata,self.screen)
        self.temporary=tempfile.TemporaryDirectory();self.directory=Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.arguments=dict(name='synthetic',conditions=self.conditions,calibration_id='calibration-1',
                            calibration=dict(sample_count=np.int64(180),target_count=9,estimated_distance_cm_median=np.float64(47.)),
                            directory=self.directory)

    def save(self, **changes):
        return save_python_profile(IDENTITY,**dict(self.arguments,**changes))

    def load(self, **changes):
        return load_python_profile(**dict(dict(name='synthetic',conditions=self.conditions,directory=self.directory),**changes))

    def summary(self):
        targets=[dict(target_id='c',region='center',target=[.5,.5]),dict(target_id='e',region='edge',target=[.05,.05])]
        frames=[dict(target_id=t['target_id'],valid=True,raw=[t['target'][0]+.02,t['target'][1]+.03],origin_m=[0,.1,.5]) for t in targets]
        return evaluate(frames,targets,self.settings.geometry,IDENTITY)

    def check(self, summary=None, **changes):
        args=dict(name='synthetic',conditions=self.conditions,calibration_id='calibration-1',
                  estimated_distance_cm_median=np.float64(48.),estimated_distance_sample_count=np.int64(40),task_id='check-1',directory=self.directory)
        return save_python_check(self.summary() if summary is None else summary,**dict(args,**changes))

    def test_python_default_folder_and_numeric_roundtrip(self):
        with patch('gaze_core.profiles.ROOT',self.directory):
            arguments=dict(self.arguments);arguments.pop('directory')
            path=save_python_profile(IDENTITY,**arguments)
            self.assertEqual(path,self.directory/'data/profiles/python/synthetic.json')
            data=load_python_profile(name='synthetic',conditions=self.conditions)
        self.assertEqual(data['pipeline'],'python');self.assertEqual(data['schema_version'],2)
        self.assertEqual(data['matrix'],IDENTITY);self.assertIs(type(data['matrix'][0][0]),float)
        self.assertEqual(data['calibration']['sample_count'],180)
        self.assertIs(type(data['calibration']['sample_count']),int)
        self.assertEqual(data['coordinates']['input'],'unfiltered_unclipped_raw_fullscreen_normalized')
        self.assertFalse(data['coordinates']['clip']);self.assertIsNone(radius_from_profile(data,self.conditions))

    def test_runtime_uses_actual_resolution_camera_index_and_screen(self):
        metadata=CameraAdapter(self.device).prepare((1280,960)).metadata
        metadata['camera_index']=np.int64(3)
        settings=replace(self.settings,camera=replace(self.settings.camera,width=320,height=240,grayscale=True))
        actual=build_conditions(settings,metadata,ScreenRect(-40,10,1920,1080,1.25))
        self.assertEqual(actual['camera']['width_px'],1280);self.assertEqual(actual['camera']['height_px'],960)
        self.assertEqual(actual['camera']['index'],3);self.assertTrue(actual['camera']['grayscale'])
        self.assertEqual(actual['screen']['x'],-40);self.assertEqual(actual['screen']['device_pixel_ratio'],1.25)
        self.assertEqual(actual['camera']['matrix'],metadata['matrix'])
        recorded_screen=dict(screen_x=0,screen_y=0,width_px=1512,height_px=982,device_pixel_ratio=2.,fullscreen=True)
        self.assertEqual(build_conditions(self.settings,self.metadata,recorded_screen),self.conditions)

    def test_semantic_fingerprint_ignores_date_rms_and_detects_same_name_edits(self):
        device=replace(self.device,calibrated_at='2027-01-01T00:00:00+00:00',reprojection_error_px=7.)
        self.assertEqual(device_fingerprint(device),device_fingerprint(self.device))
        metadata=CameraAdapter(device).prepare((640,480)).metadata
        self.assertEqual(build_conditions(replace(self.settings,device_profile=device),metadata,self.screen),self.conditions)
        changes=[dict(matrix=((611.,0.,320.),(0.,610.,240.),(0.,0.,1.))),dict(distortion=(.02,0.,0.,0.,0.)),
                 dict(resolution=(1280,960)),dict(width_cm=31.),dict(height_cm=21.),dict(screen_resolution=(1512,980)),
                 dict(horizontal_cm=2.),dict(top_cm=.4),dict(measured=False)]
        for change in changes:
            with self.subTest(change=change):self.assertNotEqual(device_fingerprint(replace(self.device,**change)),device_fingerprint(self.device))
        self.assertIsNone(device_fingerprint(None))
        self.save()
        changed=build_conditions(replace(self.settings,device_profile=replace(self.device,top_cm=1.)),self.metadata,self.screen)
        with self.assertRaisesRegex(ValueError,'设备文件语义'):self.load(conditions=changed)

    def test_each_runtime_condition_mismatch_is_rejected_with_reason(self):
        self.save()
        changes=[('camera','index',2),('camera','width_px',1280),('camera','height_px',960),
                 ('camera','grayscale',True),('camera','undistort_enabled',False),('camera','undistort_applied',False),
                 ('camera','undistort_alpha',.5),('camera','intrinsics_source','estimated'),
                 ('screen','width_px',1513),('screen','height_px',983),('screen','device_pixel_ratio',1.5),
                 ('screen','x',20),('screen','y',20),('screen','width_cm',31.),('screen','height_cm',21.),
                 ('screen','top_cm',1.),('screen','horizontal_cm',2.),('screen','measured',False)]
        for section,key,value in changes:
            with self.subTest(section=section,key=key):
                conditions=deepcopy(self.conditions);conditions[section][key]=value
                self.assertIn('条件不匹配',condition_mismatch_reason(self.conditions,conditions))
                with self.assertRaisesRegex(ValueError,'条件不匹配'):self.load(conditions=conditions)
        changed=deepcopy(self.conditions);changed['device_file']='other.yaml'
        with self.assertRaisesRegex(ValueError,'设备文件'):self.load(conditions=changed)

    def test_latest_complete_b_check_is_numeric_exact_and_sets_radius(self):
        self.save();summary=self.summary();before=deepcopy(summary)
        self.check(summary)
        profile=self.load();check=profile['latest_check']
        self.assertEqual(summary,before);self.assertEqual(check['summary'],summary)
        self.assertEqual(check['calibration_id'],'calibration-1');self.assertEqual(check['matrix_id'],profile['matrix_id'])
        self.assertEqual(check['estimated_distance_cm_median'],48.);self.assertEqual(check['estimated_distance_sample_count'],40)
        self.assertEqual(check['task_id'],'check-1')
        self.assertEqual(radius_from_profile(profile,self.conditions),summary['groups']['B']['all']['mean_cm'])
        conditions=deepcopy(self.conditions);conditions['camera']['index']=0
        self.assertIsNone(radius_from_profile(profile,conditions))

    def test_partial_unmeasured_or_other_matrix_check_preserves_previous(self):
        path=self.save();self.check();before=path.read_bytes()
        partial=self.summary();partial['groups']['B']['all']['complete']=False
        unmeasured=self.summary();unmeasured['measured']=False
        absent=self.summary();absent['groups'].pop('B')
        for summary in (partial,unmeasured,absent):
            with self.assertRaisesRegex(ValueError,'完整的 B'):self.check(summary)
            self.assertEqual(path.read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'修正矩阵'):self.check(calibration_id='other')
        self.assertEqual(path.read_bytes(),before)
        data=json.loads(before);data['matrix'][2][0]=.2
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'矩阵 ID'):self.load()

    def test_python_and_web_are_separate_and_legacy_web_needs_confirmation(self):
        screen=dict(fullscreen=True,width_px=1512,height_px=982,width_cm=30.,height_cm=20.,measured=True,top_cm=.3,horizontal_cm=0.)
        path=save_profile(IDENTITY,name='synthetic',device_file='synthetic.yaml',screen=screen,calibration_id='web-1',directory=self.directory)
        self.assertEqual(json.loads(path.read_text())['pipeline'],'web')
        with self.assertRaises(PermissionError):load_manual_web_profile(path)
        with self.assertRaises(PermissionError):load_manual_web_profile(path,confirmed=1)
        web=load_manual_web_profile(path,confirmed=True)
        self.assertEqual(web['pipeline'],'web')
        old=dict(web);old.pop('pipeline');path.write_text(json.dumps(old))
        self.assertEqual(load_manual_web_profile(path,confirmed=True)['pipeline'],'web')
        self.assertEqual(load_profile(name='synthetic',device_file='synthetic.yaml',screen=screen,geometry=self.settings.geometry,directory=self.directory)['matrix'],IDENTITY)
        with self.assertRaisesRegex(ValueError,'pipeline=python'):self.load()
        self.save()
        with self.assertRaises(ValueError):load_manual_web_profile(path,confirmed=True)
        with self.assertRaises(ValueError):load_profile(name='synthetic',device_file='synthetic.yaml',screen=screen,geometry=self.settings.geometry,directory=self.directory)

    def test_failed_save_keeps_previous_profile_and_removes_temporary(self):
        path=self.save();before=path.read_bytes()
        for failure in ('gaze_core.profiles.os.replace','gaze_core.profiles.os.fsync','gaze_core.profiles.json.dump'):
            with self.subTest(failure=failure),patch(failure,side_effect=OSError('disk full')):
                with self.assertRaises(OSError):self.save(calibration_id='new')
            self.assertEqual(path.read_bytes(),before);self.assertEqual(list(self.directory.iterdir()),[path])
        with patch('gaze_core.profiles.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.check()
        self.assertEqual(path.read_bytes(),before)

    def test_privacy_whitelist_and_invalid_numbers_cannot_overwrite(self):
        path=self.save();before=path.read_bytes()
        for field,value in [('image',b'pixels'),('base64','data:image/png;base64,abc')]:
            calibration=dict(self.arguments['calibration'],**{field:value})
            with self.assertRaises(ValueError):self.save(calibration=calibration)
            conditions=deepcopy(self.conditions);conditions['camera'][field]=value
            with self.assertRaises(ValueError):self.save(conditions=conditions)
            summary=self.summary();summary['groups']['B']['points'][0][field]=value
            with self.assertRaises(ValueError):self.check(summary)
            self.assertEqual(path.read_bytes(),before)
        for value in (True,float('nan'),float('inf'),np.float64('nan')):
            with self.subTest(value=value),self.assertRaises(ValueError):self.save(calibration=dict(sample_count=10,estimated_distance_cm_median=value))
        with self.assertRaises(ValueError):self.save(name='../unsafe')
        with self.assertRaises(ValueError):save_python_profile([[True,0],[0,1],[0,0]],**self.arguments)
        with self.assertRaises(ValueError):self.save(calibration_id='data:image/png;base64,abc')
        self.assertEqual(path.read_bytes(),before)

    def test_load_revalidates_nested_check_fields_and_numeric_values(self):
        path=self.save();self.check();original=json.loads(path.read_text())
        alterations=[lambda data:data.update(image='data:image/png;base64,abc'),
                     lambda data:data['latest_check'].update(image='data:image/png;base64,abc'),
                     lambda data:data['latest_check']['summary']['groups']['B']['all'].update(mean_cm=float('nan')),
                     lambda data:data['latest_check']['summary']['groups']['B']['points'][0].update(image='pixels'),
                     lambda data:data['latest_check'].update(matrix_id='other')]
        for alteration in alterations:
            data=deepcopy(original);alteration(data);path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):self.load()

    def test_distance_and_age_are_informational_and_do_not_expire(self):
        path=self.save();data=json.loads(path.read_text())
        data['created_at']='2020-01-01T00:00:00+00:00';data['calibration']['estimated_distance_cm_median']=60.
        path.write_text(json.dumps(data));profile=self.load()
        self.assertEqual(profile['calibration']['estimated_distance_cm_median'],60.)
        now=datetime(2026,10,20,tzinfo=timezone.utc)
        self.assertEqual(profile_age_text(dict(created_at=(now-timedelta(minutes=12)).isoformat()),now),'12 分钟前')
        self.assertEqual(profile_age_text(now.isoformat(),now),'刚刚')


if __name__=='__main__':unittest.main()
