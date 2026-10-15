"""Recorded values and source clocks survive offline replay without hardware."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import warnings

from tools.replay import ReplayError, ReplayWarning, iter_records, main, read_session, replay


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "synthetic.jsonl"

    def rows(self, source="web", body=None, *, failures=0, end=True):
        header = {"type": "session", "source": source, "version": "test-only",
                  "commit": "synthetic", "system": "fake", "device_name": "fake",
                  "inference_device": "cpu", "inference_device_name": "fake CPU",
                  "screen": {}, "camera": {"intrinsics_source": "estimated"}, "grayscale": False}
        result = [header, *(body or [])]
        if end:
            result.append({"type": "end", "written_lines": len(result) + 1,
                           "failed_records": failures, "reason": "test", "browser_time_ms": None})
        for seq, row in enumerate(result):
            row.update(schema_version=1, session_id="synthetic-test", seq=seq,
                       server_ns=9_000_000_000 + seq * 1_000_000)
            if source == "web":
                row["page_clock_id"] = "synthetic-page"
        return result

    def frame(self, time, *, source="web", valid=True, raw=None):
        result = {"type": "frame", "valid": valid, "status": "ok" if valid else "no_face",
                  "raw": raw if raw is not None else [1.2, -0.3]}
        if source == "web":
            result.update(browser_capture_ms=time, browser_request_ms=time + 1,
                          browser_response_ms=None, frame_id=f"frame-{time}")
        else:
            result.update(capture_ns=time, inference_completed_ns=time + 100)
        return result

    def write(self, rows, *, newline=True):
        self.path.write_text("\n".join(json.dumps(row) for row in rows) + ("\n" if newline else ""),
                             encoding="utf-8")
        return self.path

    def test_browser_capture_time_orders_late_frames_and_target_events(self):
        rows = self.rows(body=[
            self.frame(400),
            {"type": "target", "browser_time_ms": 200, "target": [0, 1]},
            self.frame(200, raw=[-0.00123456789, 1.23456789]),
            {"type": "calibration", "browser_time_ms": 350},
            self.frame(150, valid=False),
        ])
        session = read_session(self.write(rows))
        self.assertEqual([row["seq"] for row in session.records], [0, 5, 2, 3, 4, 1, 6])
        self.assertEqual(session.records[3]["raw"], [-0.00123456789, 1.23456789])
        self.assertIsNone(session.records[3]["browser_response_ms"])
        self.assertEqual(session.summary()["duration_seconds"], .25)
        self.assertEqual(session.summary()["frame_count"], 3)
        self.assertEqual(session.summary()["valid_rate"], 2 / 3)
        self.assertEqual(session.summary()["event_counts"], {"calibration": 1, "target": 1})
        self.assertEqual(session.summary()["status_counts"], {"no_face": 1, "ok": 2})

    def test_python_uses_capture_clock_and_preserves_session_boundaries(self):
        rows = self.rows("python", [self.frame(9_003_000_000, source="python"),
                                    {"type": "target"},
                                    self.frame(9_001_000_000, source="python")])
        session = read_session(self.write(rows))
        self.assertEqual([row["seq"] for row in session.records], [0, 3, 2, 1, 4])
        self.assertEqual(session.summary()["duration_seconds"], .004)

    def test_processor_receives_frames_only_in_capture_order(self):
        self.write(self.rows(body=[self.frame(20), {"type": "target", "browser_time_ms": 10}, self.frame(5)]))
        self.assertEqual(list(replay(self.path, lambda frame: (frame["browser_capture_ms"], frame["raw"]))),
                         [(5, [1.2, -.3]), (20, [1.2, -.3])])
        self.assertEqual(list(replay(self.path)), list(iter_records(self.path)))

    def test_complete_last_json_needs_no_newline(self):
        session = read_session(self.write(self.rows(body=[self.frame(10)]), newline=False))
        self.assertTrue(session.ended_normally)
        self.assertEqual(session.warnings, ())

    def test_truncated_final_line_and_missing_end_warn(self):
        self.write(self.rows(body=[self.frame(10)], end=False))
        with self.path.open("ab") as stream:
            stream.write(b'{"type":"fra')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            session = read_session(self.path)
        self.assertEqual(len(session.records), 2)
        self.assertFalse(session.ended_normally)
        self.assertTrue(any("截断的最后一行" in str(item.message) for item in caught))
        self.assertTrue(any("会话未正常结束" in str(item.message) for item in caught))

    def test_truncated_utf8_final_line_is_safe(self):
        self.write(self.rows(body=[self.frame(10)], end=False))
        with self.path.open("ab") as stream:
            stream.write(b'{"reason":"\xe6\x9c')
        with self.assertWarns(ReplayWarning):
            session = read_session(self.path)
        self.assertEqual(len(session.records), 2)

    def test_final_line_truncated_inside_json_tokens_is_safe(self):
        for token in ('{"value":tru', '{"value":-', '{"value":1.', '{"value":1e-', '{"value":"\\u12'):
            with self.subTest(token=token):
                self.write(self.rows(body=[self.frame(10)], end=False))
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(token)
                with self.assertWarns(ReplayWarning):
                    session = read_session(self.path)
                self.assertEqual(len(session.records), 2)

    def test_malformed_middle_line_is_not_skipped(self):
        rows = self.rows(body=[self.frame(10)])
        self.path.write_text(json.dumps(rows[0]) + '\n{"bad":}\n' + json.dumps(rows[-1]), encoding="utf-8")
        with self.assertRaisesRegex(ReplayError, "第 2 行 JSON 损坏"):
            read_session(self.path)

    def test_malformed_final_line_is_not_mistaken_for_truncation(self):
        self.write(self.rows(end=False))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write('{"bad": nope}')
        with self.assertRaisesRegex(ReplayError, "JSON 损坏"):
            read_session(self.path)

    def test_web_clock_id_mismatch_is_rejected(self):
        rows = self.rows(body=[self.frame(10)])
        rows[1]["page_clock_id"] = "different-page"
        with self.assertRaisesRegex(ReplayError, "page_clock_id 不一致"):
            read_session(self.write(rows))

    def test_missing_browser_capture_or_event_time_never_uses_server_clock(self):
        for body in ([{"type": "frame", "capture_ns": 123}], [{"type": "target"}]):
            with self.subTest(body=body), self.assertRaisesRegex(ReplayError, "browser_.*必须"):
                read_session(self.write(self.rows(body=body)))

    def test_end_count_is_checked_and_recording_failures_warn(self):
        rows = self.rows(body=[self.frame(10)], failures=2)
        with self.assertWarnsRegex(ReplayWarning, "会话录制有失败"):
            session = read_session(self.write(rows))
        self.assertEqual(session.summary()["failed_records"], 2)
        rows[-1]["written_lines"] += 1
        with self.assertRaisesRegex(ReplayError, "written_lines"):
            read_session(self.write(rows))

    def test_nested_sessions_and_rows_after_end_are_errors(self):
        rows = self.rows(body=[self.frame(10)])
        rows[1]["type"] = "session"
        with self.assertRaisesRegex(ReplayError, "嵌套"):
            read_session(self.write(rows))
        rows = self.rows(body=[self.frame(10)])
        rows[1]["type"] = "end"
        with self.assertRaisesRegex(ReplayError, "最后一条"):
            read_session(self.write(rows))

    def test_empty_file_and_non_object_rows_are_errors(self):
        self.path.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ReplayError, "第一行"):
            read_session(self.path)
        self.path.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(ReplayError, "JSON 对象"):
            read_session(self.path)

    def test_empty_session_has_no_invented_valid_rate(self):
        summary = read_session(self.write(self.rows())).summary()
        self.assertIsNone(summary["valid_rate"])
        self.assertEqual(summary["duration_seconds"], 0)

    def test_cli_rows_are_json_and_summary_is_separate(self):
        self.write(self.rows(body=[self.frame(10)]))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main([str(self.path), "--rows"])
        self.assertEqual(result, 0)
        self.assertEqual(len([json.loads(line) for line in stdout.getvalue().splitlines()]), 3)
        self.assertIn("帧数：1", stderr.getvalue())
        self.assertIn("有效率：100.00%", stderr.getvalue())

    def test_cli_runs_from_another_directory_without_installation(self):
        self.write(self.rows(body=[self.frame(10)]))
        script = Path(__file__).resolve().parents[1] / "tools" / "replay.py"
        result = subprocess.run([sys.executable, str(script), str(self.path)], cwd=self.temporary.name,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("帧数：1", result.stdout)

    def test_cli_corruption_returns_failure(self):
        self.path.write_text("{}\n", encoding="utf-8")
        with redirect_stderr(io.StringIO()) as stderr:
            result = main([str(self.path)])
        self.assertEqual(result, 1)
        self.assertIn("回放失败", stderr.getvalue())

    def test_real_recorder_roundtrip_preserves_count_timestamps_and_unclipped_coordinates(self):
        from gaze_core.config import RecordingSettings
        from gaze_core.recorder import SessionRecorder

        for source in ("web", "python"):
            with self.subTest(source=source):
                metadata = self.rows(source)[0]
                for key in ("schema_version", "type", "session_id", "seq", "server_ns"):
                    metadata.pop(key)
                metadata["page_clock_id"] = "synthetic-page" if source == "web" else None
                metadata["camera"] = {"width_px": 640, "height_px": 480,
                                      "intrinsics_source": "estimated",
                                      "matrix": [[640, 0, 320], [0, 640, 240], [0, 0, 1]],
                                      "distortion": [0, 0, 0, 0, 0]}
                settings = RecordingSettings(self.temporary.name, 16, 3)
                recorder = SessionRecorder(metadata, settings=settings, directory=self.temporary.name)
                self.addCleanup(recorder.close)
                before = time.perf_counter_ns()
                expected = []
                for index, offset in enumerate((30, 10, 20)):
                    timestamp = offset + .125 if source == "web" else before + offset
                    raw = [-.123456789 + index, 1.123456789 + index]
                    data = {"valid": True, "status": "tracking", "raw": raw,
                            "origin_m": [0, 0, .5], "gaze_vector": [0, 0, -1],
                            "head_degrees": [0, 0, 0], "distance_cm": 50,
                            "eye_openness": .4, "inference_ms": 2}
                    options = {"inference_completed_ns": before + 100 + index, "frame_id": index}
                    if source == "web":
                        options.update(browser_capture_ms=timestamp, browser_request_ms=timestamp + 1,
                                       browser_response_ms=None, request_received_ns=before + index)
                    else:
                        options["capture_ns"] = timestamp
                    self.assertTrue(recorder.record_frame(data, **options))
                    expected.append((timestamp, raw))
                recorder.close(reason="test")
                session = read_session(recorder.path)
                frames = [row for row in session.records if row["type"] == "frame"]
                field = "browser_capture_ms" if source == "web" else "capture_ns"
                self.assertEqual([(row[field], row["raw"]) for row in frames], sorted(expected))
                self.assertEqual(session.summary()["frame_count"], len(expected))
                self.assertEqual(session.records[-1]["written_lines"], len(expected) + 2)
                self.assertTrue(session.ended_normally)

    def test_nonfinite_json_and_unhashable_type_fail_cleanly(self):
        self.write(self.rows(body=[self.frame(10)]))
        self.path.write_text(self.path.read_text().replace('"raw": [1.2, -0.3]', '"raw": [NaN, 0]'))
        with self.assertRaisesRegex(ReplayError, "非有限"):
            read_session(self.path)
        rows = self.rows(body=[self.frame(10)])
        rows[1]["type"] = []
        with self.assertRaisesRegex(ReplayError, "记录类型"):
            read_session(self.write(rows))


if __name__ == "__main__":
    unittest.main()
