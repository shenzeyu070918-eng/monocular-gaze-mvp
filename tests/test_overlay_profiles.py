"""Deterministic background profile I/O; no sleeps, cameras or Qt event loop."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

from gaze_core.camera import CameraAdapter
from gaze_core.config import load_settings
from gaze_core.devices import load_device
from gaze_core.evaluation import evaluate
from gaze_core.profiles import (build_conditions, load_python_profile, profile_age_text,
                                radius_from_profile, save_profile, save_python_profile)
from overlay.mapping import ScreenRect
from overlay.profiles import ProfileManager


IDENTITY=[[1.,0.],[0.,1.],[0.,0.]]
SHIFTED=[[1.,0.],[0.,1.],[.1,-.2]]


class OverlayProfilesTests(unittest.TestCase):
    def setUp(self):
        base=load_settings(use_local=False)
        self.settings=replace(base,camera=replace(base.camera,index=1),
                              geometry=replace(base.geometry,width_cm=30.,height_cm=20.,measured=True),
                              overlay=replace(base.overlay,profile_name='builtin',profile_poll_s=1.))
        self.screen=ScreenRect(0,0,1512,982,2.)
        self.camera=CameraAdapter().prepare((640,480)).metadata
        self.conditions=build_conditions(self.settings,self.camera,self.screen)
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.now=0.;self.applied=[]
        self.manager=ProfileManager(self.settings,self.screen,on_matrix=self.applied.append,
                                    clock=lambda:self.now,directory=self.directory)
        self.addCleanup(self.manager.close)

    def write(self, matrix=IDENTITY, calibration_id='calibration-1', conditions=None):
        return save_python_profile(matrix,name='builtin',conditions=conditions or self.conditions,
                                   calibration_id=calibration_id,
                                   calibration=dict(sample_count=180,target_count=9,estimated_distance_cm_median=48.),
                                   directory=self.directory)

    def finish(self):
        """Drain bounded I/O handoffs; every wait is on an actual Future."""
        for _ in range(4):
            if self.manager._pending is None:return
            future=self.manager._pending[1]
            try:future.result(timeout=2)
            except (ValueError,OSError):pass
            self.manager.poll()
        self.fail('profile I/O did not become idle')

    def start(self):
        self.manager.observe(self.camera);self.manager.poll()
        self.assertTrue(self.manager.busy)

    def load(self):
        self.start();self.finish()

    def test_screen_resize_rejects_old_profile_with_explicit_message_then_loads_new(self):
        from overlay.screens import SCREEN_CHANGED
        self.write();self.load();self.assertIsNotNone(self.manager.matrix)
        resized=ScreenRect(0,0,1200,800,1.)
        self.manager.change_screen(resized)
        self.assertIsNone(self.manager.matrix);self.assertIsNone(self.manager.conditions)
        self.assertTrue(self.manager.screen_mismatch);self.assertIn(SCREEN_CHANGED,self.manager.reason)
        self.manager.observe(self.camera);self.manager.poll();self.finish()
        self.assertIsNone(self.manager.matrix);self.assertIn(SCREEN_CHANGED,self.manager.last_error)
        self.write(conditions=self.manager.conditions)
        self.now+=2;self.manager.poll();self.finish()
        self.assertIsNotNone(self.manager.matrix);self.assertFalse(self.manager.screen_mismatch)

    def test_startup_mirror_resolution_mismatch_has_explicit_screen_notice(self):
        self.write();self.manager.screen=ScreenRect(0,0,1200,800,1.)
        self.load()
        self.assertTrue(self.manager.screen_mismatch)
        self.assertIn('屏幕设置已变化，请重新九点校准',self.manager.last_error)

    def test_waits_for_actual_metadata_then_loads_matching_python(self):
        self.write();self.manager.poll()
        self.assertFalse(self.manager.busy);self.assertIsNone(self.manager.matrix)
        self.load()
        self.assertEqual(self.manager.conditions,self.conditions)
        self.assertEqual(self.manager.matrix,IDENTITY)
        self.assertEqual(self.manager.calibration_id,'calibration-1')
        self.assertEqual(self.applied,[IDENTITY]);self.assertIn('九点',self.manager.mode)
        self.assertEqual(self.manager.last_operation,'load')

    def test_equal_camera_metadata_skips_condition_rebuild_and_copy(self):
        camera=deepcopy(self.camera)
        with patch('overlay.profiles.build_conditions',wraps=build_conditions) as build, \
             patch('overlay.profiles.deepcopy',wraps=deepcopy) as copy:
            self.assertTrue(self.manager.observe(camera))
            for _ in range(100):
                self.assertFalse(self.manager.observe(camera))
                self.assertFalse(self.manager.observe(deepcopy(camera)))
            self.assertEqual(build.call_count,1)
            self.assertEqual(copy.call_count,1)

    def test_camera_snapshot_detects_in_place_index_matrix_and_undistort_changes(self):
        camera=deepcopy(self.camera)
        self.manager.observe(camera)
        camera['camera_index']=2
        self.assertEqual(self.manager.conditions['camera']['index'],1)
        self.assertTrue(self.manager.observe(camera))
        self.assertEqual(self.manager.conditions['camera']['index'],2)
        before=self.manager.conditions['camera']['matrix'][0][0]
        camera['matrix'][0][0]+=1.
        self.assertEqual(self.manager.conditions['camera']['matrix'][0][0],before)
        self.assertTrue(self.manager.observe(camera))
        self.assertEqual(self.manager.conditions['camera']['matrix'][0][0],before+1.)
        camera['undistort_enabled']=not camera['undistort_enabled']
        self.assertTrue(self.manager.observe(camera))
        self.assertEqual(self.manager.conditions['camera']['undistort_enabled'],camera['undistort_enabled'])

    def test_non_condition_metadata_change_is_remembered_without_invalidating_profile(self):
        self.write();self.load()
        camera=deepcopy(self.camera);camera['reprojection_error_px']=.25
        revision=self.manager.revision
        with patch('overlay.profiles.build_conditions',wraps=build_conditions) as build:
            self.assertFalse(self.manager.observe(camera))
            for _ in range(100):self.assertFalse(self.manager.observe(deepcopy(camera)))
            self.assertEqual(build.call_count,1)
        self.assertEqual(self.manager.revision,revision)
        self.assertEqual(self.manager.matrix,IDENTITY)

    def test_failed_camera_validation_does_not_cache_invalid_metadata(self):
        camera=deepcopy(self.camera);camera['camera_index']=-1
        with patch('overlay.profiles.build_conditions',wraps=build_conditions) as build:
            for _ in range(2):
                with self.assertRaises(ValueError):self.manager.observe(camera)
            self.assertEqual(build.call_count,2)
        self.assertIsNone(self.manager.conditions)

    def test_radius_validation_is_cached_until_profile_activation_or_condition_change(self):
        self.write()
        with patch('overlay.profiles.radius_from_profile',wraps=radius_from_profile) as radius:
            self.load()
            self.assertEqual(radius.call_count,1)
            targets=[dict(target_id='c',region='center',target=[.5,.5]),dict(target_id='e',region='edge',target=[.05,.05])]
            frames=[dict(target_id=t['target_id'],valid=True,raw=[t['target'][0]+.02,t['target'][1]+.03],origin_m=[0,.1,.5]) for t in targets]
            summary=evaluate(frames,targets,self.settings.geometry,IDENTITY)
            self.manager.save_check(dict(summary=summary,calibration_id='calibration-1',estimated_distance_cm_median=48.,
                                         estimated_distance_sample_count=2,task_id='check-1'))
            self.finish()
            self.assertEqual(radius.call_count,2)
            expected=summary['groups']['B']['all']['mean_cm']
            for _ in range(100):self.assertEqual(self.manager.radius,expected)
            self.assertEqual(radius.call_count,2)
            self.manager.observe(dict(self.camera,camera_index=2))
            self.assertIsNone(self.manager.radius)
            self.assertEqual(radius.call_count,2)

    def test_cached_radius_clears_after_check_file_deleted(self):
        self.write();self.load()
        targets=[dict(target_id='c',region='center',target=[.5,.5]),dict(target_id='e',region='edge',target=[.05,.05])]
        frames=[dict(target_id=t['target_id'],valid=True,raw=[t['target'][0]+.02,t['target'][1]+.03],origin_m=[0,.1,.5]) for t in targets]
        summary=evaluate(frames,targets,self.settings.geometry,IDENTITY)
        self.manager.save_check(dict(summary=summary,calibration_id='calibration-1',estimated_distance_cm_median=48.,
                                     estimated_distance_sample_count=2,task_id='check-1'))
        self.finish();self.assertIsNotNone(self.manager.radius)
        self.manager.path.unlink();self.now=2.;self.manager.poll();self.finish()
        self.assertIsNone(self.manager.radius)

    def test_mode_age_updates_at_most_once_a_second_and_invalidates_on_activation(self):
        self.write();self.load()
        with patch('overlay.profiles.profile_age_text',wraps=profile_age_text) as age:
            for _ in range(100):self.assertIn('九点',self.manager.mode)
            self.assertEqual(age.call_count,1)
            self.now=.999;self.assertIn('九点',self.manager.mode)
            self.assertEqual(age.call_count,1)
            self.now=1.;self.assertIn('九点',self.manager.mode)
            self.assertEqual(age.call_count,2)
            self.write(SHIFTED,'calibration-2');self.manager.reload();self.finish()
            self.assertIn('九点',self.manager.mode)
            self.assertEqual(age.call_count,3)
            self.manager.disable();self.assertEqual(self.manager.mode,'未校准')
            self.assertEqual(age.call_count,3)

    def test_file_poll_atomic_replacement_updates_matrix_once(self):
        self.write();self.load()
        original_stamp=self.manager.path.stat().st_ino
        self.write(SHIFTED,'calibration-2')
        self.assertNotEqual(self.manager.path.stat().st_ino,original_stamp)
        self.manager.poll();self.assertFalse(self.manager.busy)  # Poll interval has not elapsed.
        self.now=1.;self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,SHIFTED)
        self.assertEqual(self.applied,[IDENTITY,SHIFTED])
        self.now=2.;self.manager.poll();self.assertFalse(self.manager.busy)
        self.assertEqual(self.applied,[IDENTITY,SHIFTED])

    def test_deleted_and_corrupt_files_clear_active_matrix(self):
        self.write();self.load();self.manager.path.unlink()
        self.now=1.;self.manager.poll();self.finish()
        self.assertIsNone(self.manager.matrix);self.assertTrue(self.manager.last_error)
        self.assertEqual(self.applied,[IDENTITY,None])
        self.write(SHIFTED,'calibration-2');self.now=2.;self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,SHIFTED)
        self.manager.path.write_text('{',encoding='utf-8')
        self.now=3.;self.manager.poll();self.finish()
        self.assertIsNone(self.manager.matrix);self.assertTrue(self.manager.last_error)

    def test_file_replaced_after_background_read_never_applies_stale_result(self):
        self.write();self.start()
        self.manager._pending[1].result(timeout=2)
        self.write(SHIFTED,'calibration-2')
        self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,SHIFTED)
        self.assertNotIn(IDENTITY,self.applied)

    def test_signature_is_captured_before_worker_can_read_old_file(self):
        self.write()
        real_submit=self.manager._executor.submit
        def submit_and_replace(function):
            future=real_submit(function)
            future.result(timeout=2)
            self.write(SHIFTED,'newer-before-submit-return')
            return future
        with patch.object(self.manager._executor,'submit',side_effect=submit_and_replace):self.start()
        self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,SHIFTED)
        self.assertNotIn(IDENTITY,self.applied)

    def test_delete_or_corrupt_after_background_read_cannot_activate_stale_matrix(self):
        self.manager.observe(self.camera)
        for operation in ('delete','corrupt'):
            with self.subTest(operation=operation):
                self.manager.disable();self.applied.clear();self.write();self.now+=2.
                self.manager.reload()
                self.manager._pending[1].result(timeout=2)
                if operation=='delete':self.manager.path.unlink()
                else:self.manager.path.write_text('{',encoding='utf-8')
                self.manager.poll();self.finish()
                self.assertIsNone(self.manager.matrix);self.assertNotIn(IDENTITY,self.applied)
                self.assertTrue(self.manager.last_error)

    def test_frozen_task_does_not_apply_finished_load_or_poll_changed_file(self):
        self.write();self.start();self.manager._pending[1].result(timeout=2)
        self.manager.poll(frozen=True)
        self.assertTrue(self.manager.busy);self.assertIsNone(self.manager.matrix);self.assertEqual(self.applied,[])
        self.manager.poll();self.finish();self.assertEqual(self.manager.matrix,IDENTITY)
        self.write(SHIFTED,'calibration-2');self.now=2.
        self.manager.poll(frozen=True)
        self.assertFalse(self.manager.busy);self.assertEqual(self.manager.matrix,IDENTITY)
        self.manager.poll();self.finish();self.assertEqual(self.manager.matrix,SHIFTED)

    def test_condition_change_during_load_discards_old_conditions_result(self):
        self.write();entered=threading.Event();release=threading.Event()
        def delayed(**kwargs):
            entered.set()
            if not release.wait(2):raise RuntimeError('test did not release the loader')
            return load_python_profile(**kwargs)
        with patch('overlay.profiles.load_python_profile',side_effect=delayed):
            self.start();future=self.manager._pending[1]
            self.assertTrue(entered.wait(2))
            changed=CameraAdapter().prepare((1280,960)).metadata
            self.manager.observe(changed)
            release.set();future.result(timeout=2)
            self.manager.poll();self.finish()
        self.assertIsNone(self.manager.matrix);self.assertNotIn(IDENTITY,self.applied)
        self.assertIn('条件不匹配',self.manager.last_error)

    def test_condition_change_during_save_never_applies_completed_old_profile(self):
        self.manager.observe(self.camera)
        entered=threading.Event();release=threading.Event()
        def delayed(*args,**kwargs):
            entered.set()
            if not release.wait(2):raise RuntimeError('test did not release the writer')
            return save_python_profile(*args,**kwargs)
        result=dict(matrix=IDENTITY,sample_count=180,estimated_distance_cm_median=48.,calibration_id='old-condition')
        with patch('overlay.profiles.save_python_profile',side_effect=delayed):
            self.manager.save_calibration(result);future=self.manager._pending[1]
            self.assertTrue(entered.wait(2))
            self.manager.observe(CameraAdapter().prepare((1280,960)).metadata)
            release.set();future.result(timeout=2)
            self.manager.poll();self.finish()
        self.assertIsNone(self.manager.matrix);self.assertNotIn(IDENTITY,self.applied)
        self.assertIn('条件不匹配',self.manager.last_error)

    def test_save_calibration_applies_only_after_future_handoff(self):
        self.manager.observe(self.camera)
        result=dict(matrix=SHIFTED,sample_count=180,estimated_distance_cm_median=49.,calibration_id='saved-1')
        self.manager.save_calibration(result);self.manager._pending[1].result(timeout=2)
        self.assertIsNone(self.manager.matrix);self.assertEqual(self.applied,[])
        self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,SHIFTED);self.assertEqual(self.manager.calibration_id,'saved-1')
        self.assertEqual(self.manager.profile['calibration']['estimated_distance_cm_median'],49.)

    def test_file_replaced_after_calibration_save_cannot_activate_stale_matrix(self):
        self.manager.observe(self.camera)
        result=dict(matrix=IDENTITY,sample_count=180,estimated_distance_cm_median=49.,calibration_id='saved-old')
        self.manager.save_calibration(result);self.manager._pending[1].result(timeout=2)
        self.write(SHIFTED,'newer-external')
        self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,SHIFTED)
        self.assertNotIn(IDENTITY,self.applied)

    def test_save_check_is_async_and_only_completed_handoff_changes_radius(self):
        self.assertFalse(self.manager.save_check({}))
        self.write();self.load()
        targets=[dict(target_id='c',region='center',target=[.5,.5]),dict(target_id='e',region='edge',target=[.05,.05])]
        frames=[dict(target_id=t['target_id'],valid=True,raw=[t['target'][0]+.02,t['target'][1]+.03],origin_m=[0,.1,.5]) for t in targets]
        summary=evaluate(frames,targets,self.settings.geometry,IDENTITY)
        result=dict(summary=summary,calibration_id='calibration-1',estimated_distance_cm_median=48.,
                    estimated_distance_sample_count=2,task_id='check-1')
        self.assertTrue(self.manager.save_check(result));self.manager._pending[1].result(timeout=2)
        self.manager.poll(frozen=True);self.assertIsNone(self.manager.radius)
        self.manager.poll();self.finish()
        self.assertEqual(self.manager.radius,summary['groups']['B']['all']['mean_cm'])
        self.assertEqual(self.manager.profile['latest_check']['task_id'],'check-1')
        self.assertEqual(self.applied,[IDENTITY])

    def test_failed_async_save_keeps_previous_calibration(self):
        self.write();self.load()
        result=dict(matrix=SHIFTED,sample_count=180,estimated_distance_cm_median=49.,calibration_id='saved-1')
        with patch('overlay.profiles.save_python_profile',side_effect=OSError('disk full')):
            self.manager.save_calibration(result);self.finish()
        self.assertEqual(self.manager.matrix,IDENTITY);self.assertEqual(self.manager.calibration_id,'calibration-1')
        self.assertIn('disk full',self.manager.last_error)

    def test_manual_web_confirmation_and_disable_do_not_immediately_reload(self):
        self.write();self.load()
        screen=dict(fullscreen=True,width_px=1512,height_px=982)
        path=save_profile(SHIFTED,name='web',device_file=None,screen=screen,calibration_id='web-1',directory=self.directory/'web')
        with self.assertRaises(PermissionError):self.manager.load_web(path)
        with self.assertRaises(PermissionError):self.manager.load_web(path,confirmed=1)
        self.manager.load_web(path,confirmed=True);self.finish()
        self.assertEqual(self.manager.profile['pipeline'],'web');self.assertEqual(self.manager.matrix,SHIFTED)
        self.assertIn('web 档案（手动）',self.manager.mode);self.assertIsNone(self.manager.radius)
        self.now=100.;self.manager.poll()
        self.assertFalse(self.manager.busy);self.assertEqual(self.manager.matrix,SHIFTED)
        self.manager.disable();self.assertIsNone(self.manager.matrix)
        self.now=102.;self.manager.poll();self.assertFalse(self.manager.busy);self.assertIsNone(self.manager.matrix)
        self.manager.reload();self.finish()
        self.assertEqual(self.manager.profile['pipeline'],'python');self.assertEqual(self.manager.matrix,IDENTITY)

    def test_age_and_distance_do_not_cause_automatic_expiry(self):
        path=self.write();data=json.loads(path.read_text())
        data['created_at']='2020-01-01T00:00:00+00:00';data['calibration']['estimated_distance_cm_median']=70.
        path.write_text(json.dumps(data));self.load()
        self.assertEqual(self.manager.matrix,IDENTITY);self.assertIn('天前',self.manager.mode)
        self.now=10**9;self.manager.poll()
        self.assertFalse(self.manager.busy);self.assertEqual(self.manager.matrix,IDENTITY)
        self.assertEqual(self.manager.profile['calibration']['estimated_distance_cm_median'],70.)

    def test_callback_receives_copy_not_mutable_profile_matrix(self):
        self.write();self.load();self.applied[-1][2][0]=99.
        self.assertEqual(self.manager.matrix,IDENTITY)

    def test_device_comments_date_rms_do_not_invalidate_but_semantics_do(self):
        data=dict(schema_version=1,name='synthetic',
                  camera=dict(matrix=[[610.,0.,320.],[0.,610.,240.],[0.,0.,1.]],distortion=[0.]*5,
                              resolution=[640,480],reprojection_error_px=.2,calibrated_at='2026-10-20T00:00:00+00:00'),
                  screen=dict(width_cm=30.,height_cm=20.,measured=True,resolution=[1512,982]),
                  camera_offset_cm=dict(horizontal=0.,vertical=.3))
        device_path=self.directory/'device.yaml';device_path.write_text(yaml.safe_dump(data),encoding='utf-8')
        profile=load_device(device_path)
        settings=replace(self.settings,device_file=str(device_path),device_profile=profile,
                         geometry=replace(self.settings.geometry,top_cm=.3))
        camera=CameraAdapter(profile).prepare((640,480)).metadata
        conditions=build_conditions(settings,camera,self.screen)
        self.manager.close()
        self.manager=ProfileManager(settings,self.screen,on_matrix=self.applied.append,clock=lambda:self.now,directory=self.directory)
        self.addCleanup(self.manager.close)
        save_python_profile(IDENTITY,name='synthetic',conditions=conditions,calibration_id='calibration-1',
                            calibration=dict(sample_count=180,target_count=9,estimated_distance_cm_median=48.),directory=self.directory)
        self.manager.observe(camera);self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,IDENTITY)
        device_path.write_text(device_path.read_text()+'\n# New comment only.\n',encoding='utf-8')
        self.now=1.;self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,IDENTITY);self.assertEqual(self.applied,[IDENTITY])
        data['camera']['calibrated_at']='2027-01-01T00:00:00+00:00';data['camera']['reprojection_error_px']=.9
        device_path.write_text(yaml.safe_dump(data),encoding='utf-8')
        self.now=2.;self.manager.poll();self.finish()
        self.assertEqual(self.manager.matrix,IDENTITY);self.assertEqual(self.applied,[IDENTITY])
        data['camera']['matrix'][0][0]=611.
        device_path.write_text(yaml.safe_dump(data),encoding='utf-8')
        self.now=3.;self.manager.poll();self.finish()
        self.assertIsNone(self.manager.matrix);self.assertEqual(self.applied,[IDENTITY,None])
        self.assertIn('标定/几何参数已改变',self.manager.last_error)


if __name__=='__main__':unittest.main()
