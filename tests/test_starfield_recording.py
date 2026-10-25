"""Synthetic star results through the real numeric JSONL writer and replay."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from gaze_core.config import RecordingSettings
from gaze_core.recorder import SessionRecorder, validate_payload
from gaze_core.starfield import StarfieldModel, StarfieldSettings
from gaze_core.starfield_recording import starfield_record, validate_starfield_event
from tools.replay import ReplayError, read_session
from tests.test_recorder import metadata


RUN_ID = '0123456789abcdef0123456789abcdef'


def event(**updates):
    result = dict(run_id=RUN_ID, group='B', event_ns=np.int64(1_500_000_000),
                  event_code=np.int64(1), constellation_id=np.int64(-1),
                  firework_count=np.int64(1), found_count=np.int64(0), elapsed_ms=np.float64(1500.))
    result.update(updates)
    return result


def summary(**updates):
    result = dict(run_id=RUN_ID, group='C', event_ns=np.int64(60_000_000_000),
                  firework_count=np.int64(3), found_count=np.int64(2),
                  constellation_ids=[np.int64(0), np.int64(3)], elapsed_ms=np.float64(60_000.),
                  completed=np.int64(1), reason_code=np.int64(0))
    result.update(updates)
    return result


class StarfieldRecordingTests(unittest.TestCase):
    def test_real_model_events_roundtrip_with_numeric_identity_only(self):
        options = replace(StarfieldSettings(), round_s=2, replay_s=.2, bloom_ms=200)
        model = StarfieldModel(1512, 982, (2000, 2000), options, seed=4)
        model.presented(0)
        for ms in range(0, 301, 100):
            model.ingest((756, 491), ms * 1_000_000, ms * 1_000_000)
            model.update(ms * 1_000_000)
        model.update(2_000_000_000)
        model.update(2_300_000_000)
        model_events = model.drain_events()
        self.assertEqual({row['event_code'] for kind, row in model_events if kind == 'starfield_event'}, {1, 2, 3})
        self.assertEqual(model_events[-1][0], 'starfield_summary')
        model_copy = deepcopy(model_events)
        expected = [(kind, starfield_record(kind, row, run_id=RUN_ID, group='B'))
                    for kind, row in model_events]
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(metadata(), settings=RecordingSettings(folder, 64, 2))
            try:
                for kind, row in expected:
                    self.assertTrue(recorder.emit(kind, **row))
            finally:
                recorder.close('synthetic-stars')
            result = read_session(recorder.path)
            actual = [row for row in result.records if row['type'].startswith('starfield_')]
            self.assertEqual(len(actual), len(expected))
            for row, (kind, expected_payload) in zip(actual, expected):
                self.assertEqual(row['type'], kind)
                self.assertEqual({key: row[key] for key in expected_payload}, expected_payload)
            distribution = result.summary()['event_counts']
            self.assertEqual(distribution['starfield_summary'], 1)
            self.assertEqual(distribution['starfield_event'], len(expected) - 1)
        self.assertEqual(model_events, model_copy)

    def test_numpy_values_become_json_numbers_preserving_integer_codes_and_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(metadata(), settings=RecordingSettings(folder, 16, 2))
            try:
                self.assertTrue(recorder.emit('starfield_event', **event()))
                self.assertTrue(recorder.emit('starfield_summary', **summary()))
            finally:
                recorder.close('synthetic-numpy-stars')
            rows = read_session(recorder.path).records
            first, last = rows[1:3]
            for key in ('event_ns', 'event_code', 'constellation_id', 'firework_count', 'found_count'):
                self.assertIs(type(first[key]), int)
            for key in ('completed', 'reason_code', 'firework_count', 'found_count'):
                self.assertIs(type(last[key]), int)
            self.assertTrue(all(type(value) is int for value in last['constellation_ids']))
            self.assertIs(type(first['elapsed_ms']), float)
            self.assertEqual(last['constellation_ids'], [0, 3])

    def test_unknown_missing_visual_or_text_fields_are_rejected(self):
        for kind, fields in (('starfield_event', event()), ('starfield_summary', summary())):
            for key in tuple(fields):
                reduced = dict(fields)
                del reduced[key]
                with self.subTest(kind=kind, missing=key), self.assertRaises(ValueError):
                    validate_payload(kind, reduced, 'python')
            for key, value in (('image', 'data:image/png;base64,forbidden'), ('base64', b'forbidden'),
                               ('name', '北斗七星'), ('text', 'private communication'),
                               ('trajectory', [[.5, .5]]), ('positions', np.zeros((3, 2))),
                               ('app', 'stars'), ('extra', 0)):
                with self.subTest(kind=kind, extra=key), self.assertRaises(ValueError):
                    validate_payload(kind, dict(fields, **{key: value}), 'python')
            with self.assertRaises(ValueError):
                validate_payload(kind, fields, 'web')

    def test_codes_counts_identity_and_finite_values_are_strict(self):
        for values in (dict(run_id='data:image/png;base64,forbidden'), dict(run_id='A' * 32),
                       dict(group='A'), dict(event_ns=True), dict(event_ns=1.5), dict(event_ns=-1),
                       dict(event_code=0), dict(event_code=4), dict(event_code=True),
                       dict(event_code=np.float64(1)), dict(constellation_id=0),
                       dict(event_code=2, constellation_id=-1),
                       dict(event_code=2, constellation_id=0, found_count=0),
                       dict(firework_count=0), dict(firework_count=-1), dict(found_count=6),
                       dict(found_count=True), dict(elapsed_ms=np.inf), dict(elapsed_ms=np.nan),
                       dict(elapsed_ms=-1), dict(elapsed_ms=True), dict(firework_count=10 ** 400)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_starfield_event('starfield_event', event(**values))
        valid = event(event_code=2, constellation_id=np.int64(4), found_count=np.int64(1))
        validate_starfield_event('starfield_event', valid)
        validate_starfield_event('starfield_event', event(event_code=3))
        for values in (dict(constellation_ids=[0, 0]), dict(constellation_ids=[0]),
                       dict(constellation_ids=[0, 5]), dict(constellation_ids=[0, '3']),
                       dict(constellation_ids=[0, True]), dict(constellation_ids=np.array([0, 3])),
                       dict(completed=True), dict(completed=2), dict(reason_code=11),
                       dict(reason_code=-1), dict(reason_code=1), dict(elapsed_ms=np.nan)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_starfield_event('starfield_summary', summary(**values))
        validate_starfield_event('starfield_summary', summary(completed=0, reason_code=1))

    def test_metadata_helper_cannot_override_or_silently_strip_unknown_fields(self):
        base = event()
        payload = {key: value for key, value in base.items() if key not in {'run_id', 'group'}}
        result = starfield_record('starfield_event', payload, run_id=RUN_ID, group='B')
        self.assertEqual(result, base)
        self.assertNotIn('run_id', payload)
        with self.assertRaises(ValueError):
            starfield_record('starfield_event', dict(payload, run_id=RUN_ID), run_id=RUN_ID, group='B')
        with self.assertRaises(ValueError):
            starfield_record('starfield_event', dict(payload, trajectory=[]), run_id=RUN_ID, group='B')

    def test_replay_rejects_corrupt_star_payload_and_keeps_old_game_events(self):
        old_game = dict(run_id=RUN_ID, app='game', group='B', event_ns=1,
                        reason='completed', completed=True, score=1, selection_count=1,
                        false_activation_count=0, pending_count=0, reaction_count=1,
                        mean_reaction_ms=800., mean_selection_ms=800., elapsed_ms=800., hit_expand_ratio=.5)
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(metadata(), settings=RecordingSettings(folder, 16, 2))
            try:
                self.assertTrue(recorder.emit('demo_summary', **old_game))
                self.assertTrue(recorder.emit('starfield_event', **event()))
                self.assertTrue(recorder.emit('starfield_summary', **summary()))
            finally:
                recorder.close('synthetic-mixed-demo-versions')
            result = read_session(recorder.path)
            self.assertEqual(result.records[1]['app'], 'game')
            self.assertEqual(result.summary()['event_counts']['demo_summary'], 1)
            rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
            for edit in ('extra', 'missing', 'code'):
                changed = deepcopy(rows)
                if edit == 'extra':
                    changed[2]['trajectory'] = [[1, 2]]
                elif edit == 'missing':
                    del changed[2]['firework_count']
                else:
                    changed[2]['event_code'] = 99
                recorder.path.write_text(''.join(json.dumps(row) + '\n' for row in changed))
                with self.subTest(edit=edit), self.assertRaises(ReplayError):
                    read_session(recorder.path)

    def test_stars_cli_runs_from_other_directory_and_web_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = SessionRecorder(metadata(), settings=RecordingSettings(folder, 16, 2))
            try:
                self.assertTrue(recorder.emit('starfield_event', **event()))
                self.assertTrue(recorder.emit('starfield_summary', **summary()))
            finally:
                recorder.close('synthetic-stars-cli')
            script = Path(__file__).resolve().parents[1] / 'tools' / 'replay.py'
            result = subprocess.run([sys.executable, str(script), str(recorder.path)], cwd=folder,
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('starfield_summary', result.stdout)
            rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
            rows[0]['source'] = 'web'
            for row in rows:
                row['page_clock_id'] = 'synthetic-page'
            recorder.path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with self.assertRaisesRegex(ReplayError, 'Python'):
                read_session(recorder.path)


if __name__ == '__main__':
    unittest.main()
