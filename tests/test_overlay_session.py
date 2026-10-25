"""Real raw task/profile coordination with fake windows and no hardware."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from gaze_core.camera import CameraAdapter
from gaze_core.capture import Prediction
from gaze_core.config import load_settings
from gaze_core.evaluation import evaluate
from gaze_core.recorder import validate_metadata, validate_payload
from overlay.mapping import ScreenRect
from overlay.session import OverlaySession


class Signal:
    def __init__(self): self.slots = []
    def connect(self, slot): self.slots.append(slot)
    def emit(self, value):
        for slot in tuple(self.slots): slot(value)


class TaskWindow:
    """Paint acknowledgement and finish signals, without starting Qt."""
    def __init__(self, runner, screen, options, *, clock_ns):
        self.runner, self.clock_ns = runner, clock_ns
        self.completed, self.cancelled = Signal(), Signal()
        self.opened = self.closed = self.deleted = self.notified = False
    def open(self):
        self.opened = True
        self.runner.presented(self.clock_ns())
    def refresh(self):
        if self.runner.state == 'presenting':
            self.runner.presented(self.clock_ns())
        elif not self.notified and self.runner.state in {'completed','cancelled'}:
            self.notified = True
            if self.runner.state == 'completed': self.completed.emit(self.runner.result)
            else: self.cancelled.emit(self.runner.reason)
    def escape(self):
        self.runner.cancel('cancelled')
        self.refresh()
    def close(self): self.closed = True
    def deleteLater(self): self.deleted = True


class Recording:
    """Keep actual single-session ownership and strict numeric event validation."""
    def __init__(self, settings, metadata_factory, *, clock_ns, profile_path):
        self.metadata_factory, self.clock_ns, self.profile_path = metadata_factory, clock_ns, profile_path
        self.active = self.is_manual = self.closed = False
        self.task_id = None
        self.holders=set()
        self.opens = 0
        self.events, self.frames, self.ends, self.applied_file_snapshots = [], [], [], []
    def status(self): return dict(active=self.active,error=None,manual=self.is_manual)
    def manual(self, enabled):
        if enabled and not self.active: self.opens += 1
        self.is_manual = enabled
        self.active = enabled or self.task_id is not None or bool(self.holders)
        return True
    def acquire(self,owner):
        if not self.active:self.opens+=1
        self.holders.add(owner);self.active=True;return True
    def release(self,owner,reason='completed'):
        self.holders.discard(owner)
        self.active=self.is_manual or self.task_id is not None or bool(self.holders)
        return True
    def begin_task(self, task_id):
        if self.task_id is not None: return False
        if not self.active: self.opens += 1
        self.active = True; self.task_id = task_id
        return True
    def end_task(self, task_id, reason='completed'):
        if self.task_id != task_id: return False
        self.ends.append((task_id,reason)); self.task_id = None
        self.active = self.is_manual or bool(self.holders)
        return True
    def record_prediction(self, prediction):
        if self.active: self.frames.append(deepcopy(prediction))
        return True
    def emit(self, kind, **fields):
        fields.setdefault('event_ns',self.clock_ns())
        validate_payload(kind,fields,'python')
        if not self.active: return False
        if kind == 'calibration' and fields['action']=='applied':
            self.applied_file_snapshots.append(self.profile_path.read_bytes() if self.profile_path.exists() else None)
        self.events.append(dict(type=kind,**deepcopy(fields)))
        return True
    def close(self): self.closed = True; self.active = False


class Controller:
    def __init__(self):
        self.matrices = []; self.clears = 0
        self.task_active=False;self.task_states=[]
        self.click_rates=[]
    def snapshot(self): return SimpleNamespace(status='running')
    def set_matrix(self,matrix): self.matrices.append(deepcopy(matrix))
    def clear_numeric_error(self): self.clears += 1
    def set_task_active(self,active):
        self.task_active=active;self.task_states.append(active)
    def set_click_session_rate(self,active,*,max_fps=None):self.click_rates.append((active,max_fps))


class OverlaySessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'builtin.json'
        base = load_settings(use_local=False)
        self.settings = replace(base, geometry=replace(base.geometry,width_cm=30.,height_cm=20.,measured=True,top_cm=.3),
                                camera=replace(base.camera,index=1))
        self.screen = ScreenRect(20,-10,1512,982,2.)
        self.now = 1_000_000_000; self.sequence = 0
        self.notices, self.restores, self.results = [], [], []
        def recording_factory(settings, metadata_factory):
            return Recording(settings,metadata_factory,clock_ns=lambda:self.now,profile_path=self.path)
        self.session = OverlaySession(self.settings,self.screen,
                                      notice=lambda *args:self.notices.append(args),
                                      restore=lambda:self.restores.append(True),results=self.results.append,
                                      clock_ns=lambda:self.now,profile_directory=self.directory.name,
                                      recording_factory=recording_factory)
        self.addCleanup(self.session.close)
        self.controller = Controller(); self.session.controller = self.controller
        self.camera = dict(CameraAdapter().prepare((640,480)).metadata,camera_index=1,configured_camera_index=1)
        self.view = SimpleNamespace(report=dict(device='cpu',device_name='synthetic CPU'),
                                    result=Prediction(999999,0,0,dict(raw=[999.,999.],valid=True)))
        self.session.sink(self.prediction(self.now, [.4,.6]))
        self.session.tick(self.view)
        self.finish_io()

    def prediction(self, timestamp, raw=None, *, distance=48.):
        self.sequence += 1
        return Prediction(self.sequence,timestamp,timestamp+100_000_000,
                          dict(valid=raw is not None,status='tracking' if raw is not None else 'no_face',raw=raw,
                               camera=deepcopy(self.camera),origin_m=[0.,.1,.5],distance_cm=distance,
                               inference_ms=np.float64(1)))

    def test_unready_window_blocks_ingest_and_advance_but_keeps_numeric_recording(self):
        runner=self.start('evaluate')
        window=self.session.task_window
        window.accept_prediction=lambda *_:False
        self.now+=500_000_000
        prediction=self.prediction(self.now,[.3,.4])
        with patch.object(runner,'ingest') as ingest,patch.object(runner,'advance') as advance:
            self.session.sink(prediction);self.session.tick(self.view)
            ingest.assert_not_called();advance.assert_not_called()
        self.assertEqual(self.session.recording.frames[-1].sequence,prediction.sequence)
        window.accept_prediction=lambda *_:True
        self.now+=1;self.session.sink(self.prediction(self.now,[.3,.4]))
        with patch.object(runner,'ingest',wraps=runner.ingest) as ingest:
            self.session.tick(self.view);ingest.assert_called_once()
        self.session.cancel('test_complete')

    def test_screen_change_while_paused_resets_context_and_recalibrates_without_recreating_pipeline(self):
        self.load_coarse();self.assertEqual(self.session._active_group,'C')
        runner=self.start('calibrate');self.fill_task(runner);self.finish_io()
        self.assertEqual(self.session._active_group,'B')
        resized=ScreenRect(0,0,1200,800,1.)
        self.session.change_screen(resized)
        self.assertIs(self.session.controller,self.controller)
        self.assertIsNone(self.session.latest);self.assertIsNone(self.session.profiles.conditions)
        self.assertIsNone(self.session.autocal.profile);self.assertEqual(self.session._active_group,'A')
        self.assertFalse(self.session.heatmap.enabled);self.assertTrue(self.session.profiles.screen_mismatch)
        self.now+=1_000_000_000;self.session.set_running(True)
        self.session.sink(self.prediction(self.now,[.4,.6]));self.session.tick(self.view);self.finish_io()
        self.assertEqual(self.session.profiles.conditions['screen']['width_px'],1200)
        self.assertEqual(self.session.metadata()['screen']['width_px'],1200)
        self.assertEqual(self.session._active_group,'A')
        runner=self.start('calibrate');self.fill_task(runner);self.finish_io()
        self.assertEqual(self.session._active_group,'B');self.assertFalse(self.session.profiles.screen_mismatch)

    def test_unchanged_geometry_mapping_refresh_preserves_correction_and_conditions(self):
        self.load_coarse();matrix=deepcopy(self.session._active_matrix)
        conditions=deepcopy(self.session.profiles.conditions);revision=self.session.display_revision
        updated=replace(self.screen,cocoa_top=1400)
        self.session.refresh_screen_mapping(updated)
        self.assertEqual(self.session.screen,updated);self.assertEqual(self.session.profiles.conditions,conditions)
        self.assertEqual(self.session._active_matrix,matrix);self.assertEqual(self.session.display_revision,revision)

    def test_repeated_metadata_uses_snapshot_without_copying_volatile_report_counts(self):
        from overlay.session import deepcopy as real_copy
        original=self.session._metadata_snapshot
        with patch('overlay.session.deepcopy',wraps=real_copy) as copy:
            for _ in range(20):
                self.now+=100_000_000
                self.view.report['predicted_frames']=self.sequence
                self.session.sink(self.prediction(self.now,[.4,.6]))
                self.session.tick(self.view)
            copy.assert_not_called()
        self.assertIs(self.session._metadata_snapshot,original)
        # A genuine device-identity change updates metadata even with the same
        # optical conditions; live volatile counts still stay out of the cache.
        self.view.report['device']='mps'
        self.now+=1;self.session.sink(self.prediction(self.now,[.4,.6]));self.session.tick(self.view)
        self.assertIsNot(self.session._metadata_snapshot,original)
        self.assertEqual(self.session.metadata()['inference_device'],'mps')

    def load_coarse(self):
        from gaze_core.profiles import save_click_profile
        import threading
        saved=save_click_profile([.02,.03],name='builtin',count=12,conditions=self.session.profiles.conditions,
                                directory=self.directory.name)
        self.now+=2_000_000_000
        self.session.set_autocal_enabled(True)
        done=threading.Event();self.session.autocal._put(('barrier',done));self.assertTrue(done.wait(3))
        self.session.tick(self.view)
        self.assertEqual(self.session._active_group,'C')
        return saved

    def test_click_offset_check_is_C_uses_raw_and_keeps_minimal_profile_unchanged(self):
        self.load_coarse();matrix=deepcopy(self.session._active_matrix)
        self.assertIn('点击粗校准',self.session.short_mode)
        self.assertIn('12 次点击',self.session.short_mode)
        coarse=Path(self.directory.name)/'builtin.click_offset.json';before=coarse.read_bytes()
        runner=self.start('evaluate');self.assertTrue(self.session.autocal.frozen)
        with patch('overlay.tasks.fit_affine',side_effect=AssertionError('C核对不得拟合')):
            frames=self.fill_task(runner,offset=(.12,-.17))
        expected=evaluate(frames,runner.targets,self.settings.geometry,matrix)
        expected['groups']['C']=expected['groups'].pop('B')
        self.assertEqual(runner.result['summary'],expected)
        self.finish_io()
        event=next(r for r in self.session.recording.events if r['type']=='evaluation_summary')
        self.assertEqual(set(event['summary']['groups']),{'A','C'})
        self.assertEqual(self.session.radius,expected['groups']['C']['all']['mean_cm'])
        self.assertIn('最近匹配 C',self.session.radius_text)
        self.assertEqual(self.session.heatmap.context['phase']['group'],'C')
        self.assertEqual(self.session.heatmap.context['kernel']['source'],'matching_C_check')
        self.assertEqual(coarse.read_bytes(),before)
        self.assertFalse(self.path.exists())

    def test_nine_point_has_priority_and_disabling_it_activates_only_matching_coarse(self):
        self.load_coarse();coarse_matrix=deepcopy(self.session._active_matrix)
        runner=self.start('calibrate');self.fill_task(runner);self.finish_io()
        self.assertEqual(self.session._active_group,'B')
        self.assertEqual(self.controller.matrices[-1],self.session.profiles.matrix)
        self.session.profiles.disable();self.session.tick(self.view)
        self.assertEqual(self.session._active_group,'C');self.assertEqual(self.controller.matrices[-1],coarse_matrix)
        self.session.set_autocal_enabled(False)
        self.assertEqual(self.session._active_group,'A');self.assertIsNone(self.controller.matrices[-1])
        self.session.set_autocal_enabled(True);self.session.tick(self.view)
        self.assertEqual(self.session._active_group,'C')
        changed=self.prediction(self.now+1,[.5,.5]);changed.data['camera']['camera_index']=2
        self.session.sink(changed);self.now+=2;self.session.tick(self.view)
        self.assertEqual(self.session._active_group,'A');self.assertIsNone(self.session.autocal.profile)


    def finish_io(self):
        for _ in range(30):
            pending = self.session.profiles._pending
            if pending is not None:
                try: pending[1].result(timeout=3)
                except (OSError,ValueError): pass  # Initial absent profile is an expected state.
            self.session.tick(self.view)
            if not self.session.profiles.busy and self.session._saving is None: return
        self.fail('profile operations did not settle')

    def start(self, kind):
        return self.session.start(kind,None,window_factory=TaskWindow)

    def test_window_start_failure_restores_controls_and_closes_task_ownership(self):
        class BrokenWindow(TaskWindow):
            def open(self):raise RuntimeError('synthetic fullscreen failure')
        with self.assertRaisesRegex(RuntimeError,'fullscreen failure'):
            self.session.start('calibrate',None,window_factory=BrokenWindow)
        self.assertIsNone(self.session.task)
        self.assertIsNone(self.session.recording.task_id)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.session.recording.ends[-1][1],'task_window_failed')
        self.assertTrue(self.restores)
        self.assertEqual(self.controller.task_states,[True,False])

    def test_calibration_and_check_uncap_only_while_sampling_and_keep_configuration(self):
        original=self.settings.inference
        for kind in ('calibrate','evaluate'):
            with self.subTest(kind=kind):
                self.controller.task_states.clear()
                runner=self.start(kind)
                self.assertTrue(self.controller.task_active)
                self.fill_task(runner)
                self.assertFalse(self.controller.task_active)
                self.finish_io()
                self.assertEqual(self.controller.task_states,[True,False])
                self.assertIs(self.settings.inference,original)
                self.now+=1_000_000

    def test_click_protocol_runs_fresh_calibration_collection_and_same_file_public_check(self):
        class Listener:
            def __init__(self,*args,**kwargs):self.active=False
            def start(self):self.active=True;return True
            def stop(self):self.active=False
            def status(self):return dict(active=self.active,error=None)
        flow=self.session.bind_clicks(start_task=self.start,listener_factory=Listener)
        flow.begin()
        self.assertEqual(flow.state,'baseline')
        self.assertFalse(flow.listener.status()['active'])
        self.fill_task(self.session.task);self.finish_io()
        self.assertEqual(flow.state,'collecting')
        self.assertEqual(flow.baseline['calibration_id'],self.session.profiles.calibration_id)
        self.assertEqual(self.controller.click_rates[-1],(True,None))
        for i in range(3):
            self.now+=40_000_000
            self.session.sink(self.prediction(self.now,[.5,.5] if i%2 else None))
            self.session.tick(self.view)
        flow.end_collection()
        self.assertFalse(flow.listener.status()['active'])
        self.assertEqual(self.controller.click_rates[-1],(False,None))
        runner=flow.begin_check()
        self.fill_task(runner);self.finish_io()
        self.assertEqual(flow.state,'completed')
        self.assertEqual(self.session.recording.opens,1)
        self.assertFalse(self.session.recording.active)
        summary=next(e for e in self.session.recording.events if e['type']=='evaluation_summary')
        self.assertEqual(len(summary['sample_selection']),14*8)
        self.assertEqual(summary['sampling']['settle_ms'],self.settings.evaluation.settle_ms)
        end=next(e for e in self.session.recording.events if e.get('type')=='click_session' and e['action']=='completed')
        self.assertEqual(end['check_task_id'],runner.task_id)
        self.assertEqual(end['inference_frame_count'],3)
        self.assertEqual(end['actual_inference_fps'],25.)  # Analytic fake-clock rate, including invalid frames.

    def test_cancel_and_close_restore_limit_before_cleanup(self):
        for reason in ('cancelled','camera_paused','closing','recording_failed','conditions_changed'):
            with self.subTest(reason=reason):
                self.controller.task_states.clear()
                self.start('calibrate')
                self.session.cancel(reason)
                self.assertFalse(self.controller.task_active)
                self.assertEqual(self.controller.task_states,[True,False])
                self.assertFalse(self.session.active)
                self.now+=1_000_000
        self.start('calibrate')
        self.session.close()
        self.assertFalse(self.controller.task_active)

    def test_completed_analysis_failure_does_not_leave_inference_unlimited(self):
        runner=self.start('evaluate')
        self.session.results=lambda *_:(_ for _ in ()).throw(RuntimeError('synthetic panel failure'))
        with self.assertRaisesRegex(RuntimeError,'synthetic panel failure'):
            self.fill_task(runner)
        self.assertFalse(self.controller.task_active)

    def fill_task(self, runner, *, offset=(.1,-.2), distance=48.):
        frames = []
        for target in runner.targets:
            self.assertEqual(runner.state,'sampling')
            start = self.now
            for i in range(8):
                capture = start+650_000_000+i*100_000_000
                shift=offset(target) if callable(offset) else offset
                raw = [np.float64(target['target'][0]+shift[0]),np.float64(target['target'][1]+shift[1])]
                self.session.sink(self.prediction(capture,raw,distance=distance))
                frames.append(dict(target_id=target['target_id'],valid=True,raw=raw,origin_m=[0.,.1,.5]))
            # One GUI tick receives all 8 numeric frames. Its latest display
            # snapshot is deliberately unrelated and must not replace them.
            self.now = start+1_450_000_000
            self.session.tick(self.view)
            self.now = start+2_210_000_000
            self.session.tick(self.view)
        self.assertEqual(runner.state,'finishing')
        self.now += 30_000_000
        self.session.sink(self.prediction(self.now,None,distance=None))
        self.session.tick(self.view)
        self.assertEqual(runner.state,'completed')
        return frames

    def prepare_quality_prompt(self):
        choices=[]
        self.session.request_redo=lambda quality,decide:choices.append((quality,decide))
        runner=self.start('calibrate')
        self.fill_task(runner,offset=lambda p:(0.,.5) if p['target_id']=='calibration-1' else (0.,0.))
        self.assertTrue(choices)
        self.assertTrue(self.session.active)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.session.task)
        self.assertFalse(self.controller.task_active)
        with self.assertRaises(ValueError):self.start('evaluate')
        return runner,choices[0]

    def test_declining_redo_saves_first_fit_and_nine_residuals_in_profile_and_recording(self):
        first,(quality,decide)=self.prepare_quality_prompt()
        self.assertEqual(quality['unit'],'cm')
        self.assertEqual(len(quality['points']),9)
        self.assertTrue(any(p['redo'] for p in quality['points']))
        decide(False);self.finish_io()
        self.assertEqual(self.controller.task_states,[True,False])
        profile=json.loads(self.path.read_text())
        self.assertEqual(profile['matrix'],first.result['matrix'])
        self.assertEqual(profile['calibration']['quality'],quality)
        events=[e for e in self.session.recording.events if e['type']=='calibration_quality']
        self.assertEqual([e['phase'] for e in events],['initial','final'])
        self.assertIn('不是独立 B 组精度',self.results[-1])
        self.assertIsNone(self.session.radius)

    def test_redo_only_flagged_points_merges_samples_once_and_retains_one_session(self):
        first,(quality,decide)=self.prepare_quality_prompt()
        decide(True);redo=self.session.task
        self.assertTrue(self.controller.task_active)
        self.assertEqual({t['target_id'] for t in redo.targets},{p['target_id'] for p in quality['points'] if p['redo']})
        self.fill_task(redo,offset=(0.,0.),distance=40.);self.finish_io()
        self.assertFalse(self.controller.task_active)
        self.assertEqual(self.controller.task_states,[True,False,True,False])
        result=redo.result
        self.assertEqual(result['sample_count'],72+8*len(redo.targets))
        self.assertEqual(result['estimated_distance_sample_count'],result['sample_count'])
        self.assertEqual(result['quality']['round'],1)
        self.assertIsNone(self.session._pending_calibration)
        self.assertEqual(self.session.recording.opens,1)
        self.assertEqual(len(self.session.recording.ends),1)
        np.testing.assert_array_less(np.abs(np.asarray(result['matrix'])-np.eye(3,2)),np.abs(np.asarray(first.result['matrix'])-np.eye(3,2))+1e-10)

    def test_esc_in_redo_keeps_first_complete_fit_but_pause_pending_does_not_save(self):
        first,(_,decide)=self.prepare_quality_prompt()
        decide(True);self.session.task_window.escape();self.finish_io()
        self.assertFalse(self.controller.task_active)
        self.assertEqual(json.loads(self.path.read_text())['matrix'],first.result['matrix'])
        self.assertIn('redo_cancelled',[e.get('phase') for e in self.session.recording.events])
        before=self.path.read_bytes()
        self.now+=1_000_000
        self.session.request_redo=lambda quality,choice:setattr(self,'choice',choice)
        runner=self.start('calibrate')
        self.fill_task(runner,offset=lambda p:(0.,.5) if p['target_id']=='calibration-1' else (0.,0.))
        self.session.cancel('camera_paused');self.choice(True);self.finish_io()
        self.assertFalse(self.controller.task_active)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(self.session.active)

    def test_conditions_change_dismisses_prompt_and_late_choice_cannot_activate_candidate(self):
        first,(_,decide)=self.prepare_quality_prompt()
        calls=[]
        def dismiss():
            calls.append(True)
            decide(False)  # Qt rejection synchronously emits finished.
        self.session.dismiss_redo=dismiss
        changed=self.prediction(self.now+1,[.5,.5])
        changed.data['camera']['camera_index']=2
        self.session.sink(changed);self.now+=2;self.session.tick(self.view)
        decide(True);self.finish_io()
        self.assertEqual(calls,[True])
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.session.task)
        self.assertIsNone(self.session._saving)
        self.assertEqual(self.session.recording.ends[-1][1],'conditions_changed')

    def test_calibration_only_records_applied_after_atomic_save_and_counts_buffer_frames(self):
        runner = self.start('calibrate')
        window = self.session.task_window
        self.fill_task(runner)
        self.finish_io()
        self.assertTrue(self.path.exists())
        profile = json.loads(self.path.read_text())
        self.assertEqual(profile['pipeline'],'python')
        self.assertEqual(profile['calibration']['sample_count'],72)
        self.assertEqual(profile['calibration']['estimated_distance_cm_median'],48.)
        np.testing.assert_allclose(profile['matrix'],[[1,0],[0,1],[-.1,.2]],atol=1e-12)
        applied = [event for event in self.session.recording.events if event['type']=='calibration' and event['action']=='applied']
        self.assertEqual(len(applied),1)
        self.assertEqual(applied[0]['estimated_distance_sample_count'],72)
        self.assertEqual(applied[0]['estimated_distance_cm_median'],48.)
        self.assertEqual(applied[0]['calibration_id'],profile['calibration_id'])
        saved_at_emit = json.loads(self.session.recording.applied_file_snapshots[0])
        self.assertEqual(saved_at_emit['calibration_id'],profile['calibration_id'])
        self.assertEqual(self.controller.matrices[-1],profile['matrix'])
        self.assertTrue(window.closed and window.deleted)
        self.assertEqual(len(self.restores),1)
        self.assertFalse(self.session.active)
        self.assertIsNone(self.session.radius)
        self.assertIn('配置示意值',self.session.radius_text)

    def test_escape_cancels_without_creating_or_replacing_profile_or_matrix(self):
        runner = self.start('calibrate')
        self.session.task_window.escape()
        self.assertEqual(runner.reason,'cancelled')
        self.assertFalse(self.path.exists())
        self.assertFalse(self.session.active)
        self.assertEqual(self.session.recording.ends[-1][1],'cancelled')
        runner = self.start('calibrate'); self.fill_task(runner); self.finish_io()
        old_bytes = self.path.read_bytes(); old_matrix = deepcopy(self.session.profiles.matrix)
        self.now += 1_000_000
        runner = self.start('calibrate')
        capture = self.now+700_000_000
        self.session.sink(self.prediction(capture,[.9,.9]))
        self.now = capture+100_000_000; self.session.tick(self.view)
        self.session.task_window.escape(); self.finish_io()
        self.assertEqual(runner.state,'cancelled')
        self.assertEqual(self.path.read_bytes(),old_bytes)
        self.assertEqual(self.session.profiles.matrix,old_matrix)
        self.assertIsNone(runner.result)

    def test_check_matches_shared_web_statistics_records_distance_and_updates_matching_radius(self):
        calibration = self.start('calibrate'); self.fill_task(calibration); self.finish_io()
        matrix = deepcopy(self.session.profiles.matrix)
        self.now += 1_000_000
        runner = self.start('evaluate')
        with patch('overlay.tasks.fit_affine',side_effect=AssertionError('核对不得拟合')):
            frames = self.fill_task(runner,offset=(.12,-.17),distance=np.float64(47.5))
        expected = evaluate(frames,runner.targets,self.settings.geometry,matrix)
        self.assertEqual(runner.result['summary'],expected)
        self.finish_io()
        event = next(event for event in self.session.recording.events if event['type']=='evaluation_summary')
        self.assertEqual(event['summary'],expected)
        self.assertEqual(event['estimated_distance_cm_median'],47.5)
        self.assertEqual(event['estimated_distance_sample_count'],14*8)
        self.assertNotIn('browser_time_ms',event)
        profile = json.loads(self.path.read_text()); check = profile['latest_check']
        self.assertEqual(check['summary'],expected)
        self.assertEqual(check['matrix_id'],profile['matrix_id'])
        self.assertEqual(check['calibration_id'],profile['calibration_id'])
        self.assertEqual(self.session.radius,expected['groups']['B']['all']['mean_cm'])
        self.assertIn('最近匹配 B 核对',self.session.radius_text)
        # A new matrix must discard the previous matrix's radius provenance.
        self.now += 1_000_000
        newer = self.start('calibrate'); self.fill_task(newer,offset=(.2,-.1)); self.finish_io()
        self.assertIsNone(self.session.radius)
        self.assertIsNone(json.loads(self.path.read_text())['latest_check'])

    def test_task_borrows_manual_recording_and_does_not_close_it(self):
        recording = self.session.recording
        self.assertTrue(recording.manual(True))
        runner = self.start('calibrate'); self.fill_task(runner); self.finish_io()
        self.assertEqual(recording.opens,1)
        self.assertTrue(recording.status()['active'])
        self.assertTrue(recording.status()['manual'])
        self.now += 1_000_000
        runner = self.start('evaluate'); self.fill_task(runner); self.finish_io()
        self.assertEqual(recording.opens,1)
        self.assertTrue(recording.status()['active'])
        self.assertEqual(len(recording.ends),2)

    def test_metadata_uses_actual_python_camera_and_complete_logical_screen(self):
        metadata = self.session.metadata()
        validate_metadata(metadata)
        self.assertEqual(metadata['source'],'python')
        self.assertEqual(metadata['pipeline'],'python')
        self.assertIsNone(metadata['page_clock_id'])
        self.assertEqual(metadata['camera']['camera_index'],1)
        self.assertEqual((metadata['camera']['width_px'],metadata['camera']['height_px']),(640,480))
        self.assertEqual((metadata['screen']['width_px'],metadata['screen']['height_px']),(1512,982))
        self.assertEqual((metadata['screen']['screen_x'],metadata['screen']['screen_y']),(20,-10))
        self.assertEqual(metadata['screen']['device_pixel_ratio'],2.)
        self.assertTrue(metadata['screen']['fullscreen'])
        self.assertEqual(len(metadata['conditions_fingerprint']),64)
        self.assertNotIn('landmarks',json.dumps(metadata))
        self.assertNotIn('base64',json.dumps(metadata))

    def test_changed_conditions_and_buffer_overflow_cancel_incomplete_task(self):
        runner = self.start('calibrate')
        changed = self.prediction(self.now+700_000_000,[.5,.5])
        changed.data['camera']['camera_index'] = 2
        self.session.sink(changed); self.now += 800_000_000
        self.session.tick(self.view)
        self.assertEqual(runner.reason,'conditions_changed')
        self.assertFalse(self.path.exists())
        self.finish_io()
        self.session.sink(self.prediction(self.now+1,[.5,.5])); self.now += 2
        self.session.tick(self.view); self.finish_io()
        runner = self.start('calibrate')
        # A consumer overflow must be explicit and never create a partial fit.
        self.session.buffer._error = 'synthetic queue overflow'
        self.session.tick(self.view)
        self.assertEqual(runner.reason,'numeric_results_incomplete')
        self.assertFalse(self.path.exists())
        self.assertTrue(any('不完整' in text for text,_ in self.notices))


if __name__ == '__main__':
    unittest.main()
