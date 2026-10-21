"""Click-session protocol with fake native input, clocks and single-file leases."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from gaze_core.config import load_settings
from gaze_core.recorder import validate_payload
from overlay.click_session import ClickSession


class FakeRecording:
    """Same ownership contract as PythonRecording, without a disk writer."""
    def __init__(self):
        self.manual = False
        self.task = None
        self.holders = set()
        self.active = False
        self.error = None
        self.events = []
        self.open_count = 0
        self.closed_reasons = []
        self.written_clicks = self.excluded_clicks = 0
        self.reject_next = False

    def _open(self):
        if self.error:
            return False
        if not self.active:
            self.active = True
            self.open_count += 1
        return True

    def _release(self, reason):
        if self.active and not self.holders and self.task is None and not self.manual:
            self.active = False
            self.closed_reasons.append(reason)

    def acquire(self, owner):
        if not self._open():
            return False
        self.holders.add(owner)
        return True

    def release(self, owner, reason='owner_completed'):
        if owner not in self.holders:
            return False
        self.holders.remove(owner)
        self._release(reason)
        return True

    def begin_task(self, task_id):
        if self.task is not None or not self._open():
            return False
        self.task = task_id
        return True

    def end_task(self, task_id, reason='task_completed'):
        if task_id != self.task:
            return False
        self.task = None
        self._release(reason)
        return True

    def emit(self, kind, **fields):
        validate_payload(kind, fields, 'python')
        if self.reject_next:
            self.reject_next = False
            self.error = 'synthetic writer failure'
            self.holders.clear()
            self.task = None
            self.manual = self.active = False
            return False
        if not self.active:
            return False
        self.events.append(dict(type=kind, **deepcopy(fields)))
        return True

    def status(self):
        return dict(active=self.active, error=self.error, holders=sorted(self.holders),
                    written_clicks=self.written_clicks, excluded_clicks=self.excluded_clicks)


class FakeListener:
    def __init__(self, screen, emit, *, exclude):
        self.emit = emit
        self.running = False
        self.starts = self.stops = self.accepted = 0
        self.error = None
        self.start_error = False

    def start(self):
        if self.start_error:
            raise RuntimeError('synthetic listener failure')
        if not self.running:
            self.running = True
            self.starts += 1
        return True

    def stop(self):
        if self.running:
            self.stops += 1
        self.running = False

    def status(self):
        return dict(active=self.running, error=self.error, accepted=self.accepted)


class ClickSessionTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(use_local=False)
        self.now_ns = 1_000_000_000
        self.wall_now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
        self.recording = FakeRecording()
        self.conditions = dict(camera_index=1, camera_resolution=[640,480], undistort=True)
        self.profiles = SimpleNamespace(conditions=deepcopy(self.conditions), profile=None, calibration_id='none')
        self.profiles.profile = self.profile(age_s=12)
        self.profiles.calibration_id = self.profiles.profile['calibration_id']
        self.tasks, self.rate_calls, self.notices = [], [], []
        self.session = ClickSession(self.settings, object(), self.recording, self.profiles,
                                    start_task=self.start_task, set_rate=self.set_rate,
                                    notice=lambda text,error:self.notices.append((text,error)),
                                    listener_factory=FakeListener, clock_ns=lambda:self.now_ns,
                                    wall_clock=lambda:self.wall_now)

    def profile(self, *, age_s=0, calibration_id='fresh-nine', pipeline='python'):
        return dict(pipeline=pipeline, calibration_id=calibration_id, conditions=deepcopy(self.conditions),
                    created_at=(self.wall_now-timedelta(seconds=age_s)).isoformat(),
                    matrix=[[1.,0.],[0.,1.],[.1,-.2]])

    def start_task(self, kind):
        runner = SimpleNamespace(kind=kind, task_id=f'{kind}-{len(self.tasks)+1}')
        if not self.recording.begin_task(runner.task_id):
            raise RuntimeError('synthetic nested task')
        self.tasks.append(runner)
        return runner

    def set_rate(self, active, **kwargs):
        self.rate_calls.append((active, kwargs))

    def baseline_saved(self, profile=None):
        # OverlaySession calls baseline_ready only after the background save
        # has reloaded and matched the finished Python calibration profile.
        runner = self.tasks[-1]
        self.recording.end_task(runner.task_id)
        self.profiles.profile = profile or self.profile(age_s=0)
        self.profiles.calibration_id = self.profiles.profile['calibration_id']
        self.session.baseline_ready(self.profiles.profile)

    def collecting(self):
        self.session.begin()
        self.baseline_saved()

    def actions(self):
        return [event['action'] for event in self.recording.events if event['type']=='click_session']

    def test_default_off_and_begin_always_requests_fresh_nine(self):
        self.assertFalse(self.session.active)
        self.assertFalse(self.session.listener.running)
        self.assertEqual(self.session.listener.starts, 0)
        self.assertEqual(self.recording.open_count, 0)
        self.assertEqual(self.rate_calls, [])
        self.session.begin()
        self.assertEqual([task.kind for task in self.tasks], ['calibrate'])
        self.assertEqual(self.session.state, 'baseline')
        self.assertEqual(self.recording.holders, {'experiment'})
        self.assertFalse(self.session.listener.running)
        self.assertEqual(self.actions(), ['started'])

    def test_pending_profile_save_does_not_collect_or_select_old_profile(self):
        self.session.begin()
        self.recording.end_task(self.tasks[-1].task_id)
        self.now_ns += 2_000_000_000
        self.session.tick()
        self.session.observe(SimpleNamespace(captured_ns=self.now_ns, data=dict(valid=True)))
        self.assertEqual(self.session.state, 'baseline')
        self.assertIsNone(self.session.baseline)
        self.assertEqual(self.session.listener.starts, 0)
        self.assertEqual(self.session._timings()['inference_frame_count'], 0)
        self.assertFalse(any(active for active, _ in self.rate_calls))

    def test_new_baseline_records_time_age_and_starts_fullspeed_collection(self):
        self.session.begin()
        fresh = self.profile(age_s=1.25)
        self.baseline_saved(fresh)
        self.assertEqual(self.session.state, 'collecting')
        self.assertEqual(self.rate_calls[-1], (True, {'max_fps':None}))
        self.assertTrue(self.session.listener.running)
        self.assertEqual(self.recording.holders, {'experiment','clicks'})
        event = next(row for row in self.recording.events if row.get('action')=='baseline')
        self.assertEqual(event['baseline_created_at'], fresh['created_at'])
        self.assertEqual(event['baseline_age_s'], 1.25)
        self.assertEqual(event['baseline_matrix'], fresh['matrix'])
        fresh['matrix'][2][0] = 99
        self.assertEqual(self.session.baseline['matrix'][2][0], .1)
        self.assertEqual(self.actions(), ['started','baseline','collecting'])

    def test_explicit_reuse_requires_matching_python_profile_younger_than_300_seconds(self):
        self.profiles.profile = self.profile(age_s=299.999)
        self.assertTrue(self.session.recent_baseline())
        self.session.begin(reuse_recent=True)
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.session.state, 'collecting')
        self.assertAlmostEqual(self.session.baseline['age_s'], 299.999)
        self.session.abort()
        for profile in (self.profile(age_s=300), self.profile(age_s=301), self.profile(age_s=-1),
                        self.profile(age_s=1, pipeline='web')):
            self.profiles.profile = profile
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.session.begin(reuse_recent=True)
        self.profiles.profile = self.profile(age_s=1)
        self.profiles.profile['conditions']['camera_index'] = 0
        self.assertFalse(self.session.recent_baseline())

    def test_configured_collection_limit_is_separate_from_normal_display_limit(self):
        settings = replace(self.settings, click_session=replace(self.settings.click_session, max_fps=27.))
        self.session.settings = settings
        self.collecting()
        self.assertEqual(self.rate_calls[-1], (True, {'max_fps':27.}))
        self.session.end_collection()
        self.assertEqual(self.rate_calls[-1], (False, {}))
        self.assertEqual(self.settings.inference.max_fps, 15.)

    def test_collection_counts_raw_invalid_frames_and_reports_actual_capture_spacing(self):
        self.collecting()
        start = self.now_ns
        self.session.observe(SimpleNamespace(captured_ns=start-1, data=dict(valid=True)))
        for index, valid in enumerate((True, False, True)):
            self.session.observe(SimpleNamespace(captured_ns=start+index*100_000_000,
                                                 data=dict(valid=valid)))
        self.now_ns += 250_000_000
        self.session.end_collection()
        event = self.recording.events[-1]
        self.assertEqual(event['action'], 'collection_ended')
        self.assertEqual(event['inference_frame_count'], 3)
        self.assertEqual(event['actual_inference_fps'], 10.)
        self.assertEqual(event['collection_start_ns'], start)
        self.assertEqual(event['collection_end_ns'], self.now_ns)
        self.assertFalse(self.session.listener.running)
        self.assertEqual(self.recording.holders, {'experiment'})
        self.assertTrue(self.recording.active)

    def test_final_check_completes_one_session_file_with_same_baseline(self):
        self.collecting()
        self.session.end_collection()
        check = self.session.begin_check()
        self.assertEqual(check.kind, 'evaluate')
        self.recording.end_task(check.task_id)
        self.session.completed_check(dict(task_id=check.task_id))
        self.assertEqual(self.session.state, 'completed')
        self.assertFalse(self.recording.active)
        self.assertEqual(self.recording.open_count, 1)
        self.assertEqual(self.recording.holders, set())
        self.assertEqual(self.recording.closed_reasons, ['click_session_completed'])
        self.assertEqual(self.recording.events[-1]['check_task_id'], check.task_id)
        self.assertEqual(self.recording.events[-1]['baseline_calibration_id'], 'fresh-nine')
        self.assertEqual(self.actions(), ['started','baseline','collecting','collection_ended','check_started','completed'])

    def test_check_cancel_keeps_session_for_retry_and_abort_never_marks_complete(self):
        self.collecting()
        self.session.end_collection()
        first = self.session.begin_check()
        self.recording.end_task(first.task_id, 'cancelled')
        self.session.cancelled_task('evaluate', 'cancelled')
        self.assertEqual(self.session.state, 'awaiting_check')
        self.assertIsNone(self.session.check_task_id)
        self.assertTrue(self.recording.active)
        second = self.session.begin_check()
        self.assertNotEqual(second.task_id, first.task_id)
        self.recording.end_task(second.task_id, 'cancelled')
        self.session.cancelled_task('evaluate','cancelled')
        self.session.abort('user_aborted')
        self.assertEqual(self.session.state, 'aborted')
        self.assertNotIn('completed', self.actions())
        self.assertEqual(self.recording.open_count, 1)

    def test_changed_baseline_refuses_public_check(self):
        self.collecting()
        self.session.end_collection()
        self.profiles.calibration_id = 'replacement-nine'
        with self.assertRaisesRegex(ValueError, 'B 基线'):
            self.session.begin_check()
        self.assertEqual(self.session.state, 'aborted')
        self.assertEqual(self.recording.events[-1]['reason'], 'baseline_changed')
        self.assertEqual(self.recording.holders, set())

    def test_conditions_change_and_listener_error_stop_capture_and_release_experiment(self):
        self.collecting()
        self.profiles.conditions['camera_index'] = 0
        self.session.tick()
        self.assertEqual(self.session.state, 'aborted')
        self.assertFalse(self.session.listener.running)
        self.assertFalse(self.recording.active)
        self.assertEqual(self.recording.events[-1]['reason'], 'conditions_changed')

    def test_listener_failure_cleans_up_without_holding_a_file_open(self):
        self.session.listener.start_error = True
        self.session.begin()
        self.baseline_saved()
        self.assertEqual(self.session.state, 'aborted')
        self.assertEqual(self.recording.holders, set())
        self.assertEqual(self.rate_calls[-1], (False, {}))
        self.assertTrue(self.notices[-1][1])

    def test_runtime_listener_failure_and_explicit_pause_or_exit_release_all_click_leases(self):
        self.collecting()
        self.session.listener.error = 'native monitor failed'
        self.session.tick()
        self.assertEqual(self.session.state, 'aborted')
        self.assertEqual(self.recording.events[-1]['reason'], 'click_listener_failed')
        self.assertTrue(self.notices[-1][1])
        self.session.listener.error = None
        for reason in ('camera_paused', 'overlay_closed'):
            self.collecting()
            self.session.abort(reason)
            self.assertFalse(self.session.listener.running)
            self.assertEqual(self.recording.holders, set())
            self.assertFalse(self.recording.active)
            self.assertEqual(self.rate_calls[-1], (False, {}))
            self.assertEqual(self.recording.events[-1]['reason'], reason)

    def test_recording_failure_during_baseline_stops_collection_and_clears_holders(self):
        self.session.begin()
        self.recording.reject_next = True
        self.baseline_saved()
        self.assertEqual(self.session.state, 'aborted')
        self.assertFalse(self.session.listener.running)
        self.assertEqual(self.recording.holders, set())
        self.assertEqual(self.rate_calls[-1], (False, {}))
        self.assertTrue(self.notices[-1][1])

    def test_recording_failure_during_check_completion_is_aborted_and_visible(self):
        self.collecting()
        self.session.end_collection()
        check = self.session.begin_check()
        self.recording.end_task(check.task_id)
        self.recording.reject_next = True
        self.session.completed_check(dict(task_id=check.task_id))
        self.assertEqual(self.session.state, 'aborted')
        self.assertEqual(self.recording.holders, set())
        self.assertFalse(self.recording.active)
        self.assertTrue(self.notices[-1][1])
        self.assertNotIn('completed', self.actions())

    def test_baseline_replacement_during_collection_aborts_on_next_tick(self):
        self.collecting()
        self.profiles.calibration_id = 'replacement-nine'
        self.session.tick()
        self.assertEqual(self.session.state, 'aborted')
        self.assertFalse(self.session.listener.running)
        self.assertEqual(self.recording.holders, set())
        self.assertEqual(self.recording.events[-1]['reason'], 'baseline_changed')

    def test_standalone_capture_has_its_own_lease_and_never_counts_enqueued_as_flushed(self):
        self.session.set_capture(True)
        self.assertEqual(self.recording.holders, {'clicks'})
        self.assertEqual(self.session.state, 'idle')
        self.assertEqual(self.rate_calls, [])
        # Listener admission counters are deliberately separate from the only
        # counters a panel may label "recorded": recorder flush confirmations.
        self.session.listener.accepted = 8
        self.assertEqual(self.recording.status()['written_clicks'], 0)
        self.recording.written_clicks = 5
        self.assertEqual(self.recording.status()['written_clicks'], 5)
        self.session.set_capture(False)
        self.assertFalse(self.session.listener.running)
        self.assertEqual(self.recording.holders, set())
        self.assertFalse(self.recording.active)

    def test_close_also_stops_standalone_capture(self):
        self.session.set_capture(True)
        self.session.close()
        self.assertFalse(self.session.listener.running)
        self.assertFalse(self.recording.active)
        self.assertEqual(self.recording.holders, set())


if __name__ == '__main__':
    unittest.main()
