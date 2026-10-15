"""Privacy and writer lifecycle checks using synthetic numeric data and files."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import threading
import unittest
import warnings

import numpy as np

from gaze_core.config import RecordingSettings
from gaze_core.geometry import intersect_screen
from gaze_core.recorder import SessionRecorder
from tools.replay import ReplayWarning, read_session


IMAGE_DATA = "data:image/jpeg;base64,AAABBBTESTIMAGE"


def metadata(source="python"):
    return {
        "source": source, "version": "synthetic-test", "commit": "synthetic-test",
        "system": "synthetic-test", "device_name": "synthetic-test",
        "inference_device": "cpu", "inference_device_name": "synthetic CPU",
        "screen": {"width_px": 1000, "height_px": 800, "width_cm": None,
                   "height_cm": None, "measured": False, "device_pixel_ratio": 1,
                   "window_width_px": 1000, "window_height_px": 800, "fullscreen": True},
        "camera": {"width_px": 640, "height_px": 480, "intrinsics_source": "estimated",
                   "matrix": [[640, 0, 320], [0, 640, 240], [0, 0, 1]], "distortion": [0, 0, 0, 0, 0]},
        "grayscale": False, "page_clock_id": "synthetic-page" if source == "web" else None,
    }


def frame():
    return {"valid": True, "status": "tracking", "raw": [-.25, 1.25],
            "origin_m": [0, 0, .5], "gaze_vector": [0, 0, -1], "head_degrees": [0, 0, 0],
            "distance_cm": 50, "eye_openness": .4, "inference_ms": 1}


def target(**updates):
    result = {"action": "appear", "task_type": "jump", "task_id": "synthetic-task",
              "index": 0, "target": [0, 1], "browser_time_ms": 20}
    result.update(updates)
    return result


class ObservedOutput:
    """Real temporary file with controllable delay/failure; never touches hardware."""
    def __init__(self, *, block_frame=False, fail_frame=False, truncate_frame=False):
        self.block_frame, self.fail_frame, self.truncate_frame = block_frame, fail_frame, truncate_frame
        self.blocked = threading.Event()
        self.resume = threading.Event()
        self.condition = threading.Condition()
        self.operations = []
        self.flush_count = 0
        self.frame_writes = 0

    def __call__(self, path, mode, *, encoding):
        self.output = open(path, mode, encoding=encoding)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.output.close()

    def write(self, text):
        row = json.loads(text)
        self.operations.append(("write", row["type"], threading.current_thread().name))
        if row["type"] == "frame":
            self.frame_writes += 1
            if self.block_frame and self.frame_writes == 1:
                self.blocked.set()
                if not self.resume.wait(3):
                    raise RuntimeError("synthetic writer gate timed out")
            if self.truncate_frame:
                # A complete prefix is on disk; the interrupted final line is not.
                self.output.write(text[:-3])
                self.output.flush()
                raise OSError("synthetic interrupted disk write")
            if self.fail_frame:
                raise OSError("synthetic disk full")
        self.output.write(text)

    def flush(self):
        self.output.flush()
        with self.condition:
            self.flush_count += 1
            self.operations.append(("flush", None, threading.current_thread().name))
            self.condition.notify_all()

    def wait_for_flushes(self, count):
        with self.condition:
            if not self.condition.wait_for(lambda: self.flush_count >= count, 3):
                raise AssertionError("synthetic writer did not flush")


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_clock_units_are_compatible_with_replay(self):
        recorder = self.recorder()
        for capture_ns, completed_ns in [(-1,2),(1.5,2),(1,-2),(1,2.5)]:
            with self.subTest(capture_ns=capture_ns,completed_ns=completed_ns), self.assertRaises(ValueError):
                recorder.record_frame(frame(),capture_ns=capture_ns,inference_completed_ns=completed_ns)
        web = self.recorder(source='web')
        with self.assertRaises(ValueError):
            web.record_frame(frame(),browser_capture_ms=-.5,inference_completed_ns=2)

    def test_numpy_geometry_roundtrip_preserves_coordinates_and_integer_nanoseconds(self):
        raw=intersect_screen(np.array([1.,.3,.5]),np.array([0.,0.,-1.]),30.2,19.6)
        self.assertTrue(all(isinstance(value,np.floating) for value in raw))
        data=dict(frame(),raw=raw,origin_m=[np.float32(1),np.float64(.3),np.float16(.5)],
                  gaze_vector=[np.int32(0),np.int64(0),np.float64(-1)],
                  head_degrees=[Fraction(1,2),np.float32(0),np.float64(0)],
                  distance_cm=np.float32(50),eye_openness=np.float64(.4),inference_ms=np.int64(2))
        recorder=self.recorder()
        self.assertTrue(self.record_frame(recorder,dict(frame(),raw=raw)))
        timestamp=2**53+7  # Exact synthetic clock; converting this to float loses a nanosecond.
        self.assertTrue(self.record_frame(recorder,data,capture_ns=np.int64(timestamp),
                        inference_completed_ns=np.int64(timestamp+37),frame_id=np.int64(17)))
        recorder.close()
        session=read_session(recorder.path)
        row=session.records[2]
        self.assertEqual(session.summary()['frame_count'],2)
        self.assertEqual(row['raw'],[float(v) for v in raw])
        self.assertLess(row['raw'][0],0)
        for key in ('raw','origin_m','gaze_vector','head_degrees'):
            self.assertTrue(all(type(v) is float for v in row[key]),key)
        for key in ('distance_cm','eye_openness','inference_ms'):
            self.assertIs(type(row[key]),float,key)
        self.assertEqual((row['capture_ns'],row['inference_completed_ns'],row['frame_id']),
                         (timestamp,timestamp+37,17))
        for key in ('capture_ns','inference_completed_ns','frame_id','seq','server_ns','schema_version'):
            self.assertIs(type(row[key]),int,key)

    def test_real_scalars_are_normalized_in_metadata_events_and_end(self):
        meta=metadata('web')
        meta['screen']['width_px']=np.int64(1000)
        meta['camera']['matrix'][0][0]=np.float32(640)
        recorder=SessionRecorder(meta,directory=self.temporary.name)
        self.addCleanup(recorder.close)
        self.assertTrue(recorder.emit('target',**target(index=np.int32(0),
                        target=[np.float32(.25),np.int64(1)],browser_time_ms=np.float32(20.5))))
        for index,kind in enumerate((float,int,np.float16,np.float32,np.float64,np.longdouble,Fraction)):
            value=kind(1)
            self.assertTrue(self.record_frame(recorder,dict(frame(),inference_ms=value),frame_id=index))
        recorder.close(browser_time_ms=np.float64(30.5))
        session=read_session(recorder.path)
        self.assertIs(type(session.metadata['screen']['width_px']),float)
        self.assertIs(type(session.metadata['camera']['matrix'][0][0]),float)
        event=next(row for row in session.records if row['type']=='target')
        self.assertEqual(event['target'],[.25,1.])
        self.assertTrue(all(type(v) is float for v in event['target']))
        self.assertIs(type(event['index']),int)
        self.assertIs(type(event['browser_time_ms']),float)
        self.assertIs(type(session.records[-1]['browser_time_ms']),float)

    def test_bool_nonfinite_and_nonreal_scalars_are_rejected(self):
        recorder=self.recorder()
        for bad in (True,False,np.bool_(True),float('nan'),float('inf'),-float('inf'),
                    np.float32('nan'),np.float64('inf'),np.longdouble('-inf'),complex(1,0),np.complex64(1)):
            with self.subTest(value=repr(bad)),self.assertRaises(ValueError):
                self.record_frame(recorder,dict(frame(),raw=[bad,.5]))
            with self.subTest(scalar=repr(bad)),self.assertRaises(ValueError):
                self.record_frame(recorder,dict(frame(),inference_ms=bad))
        recorder.close()
        self.assertEqual(len(read_session(recorder.path).records),2)

    def recorder(self, *, source="python", writer=None, queue_size=16):
        settings = RecordingSettings(self.temporary.name, queue_size, 3)
        recorder = SessionRecorder(metadata(source), directory=self.temporary.name, settings=settings,
                                   file_factory=writer)
        self.addCleanup(recorder.close)
        if writer is not None:
            self.addCleanup(writer.resume.set)
        return recorder

    def record_frame(self, recorder, data=None, **updates):
        options = {"capture_ns": 100, "inference_completed_ns": 150, "frame_id": 1}
        if recorder.source == "web":
            options.update(browser_capture_ms=10, browser_request_ms=11,
                           browser_response_ms=None, request_received_ns=120)
        options.update(updates)
        return recorder.record_frame(data if data is not None else frame(), **options)

    def test_unknown_image_base64_bytes_and_nested_payloads_are_rejected(self):
        recorder = self.recorder(source="web")
        for key, value in (("image", IMAGE_DATA), ("base64", IMAGE_DATA), ("bytes", b"PNG"),
                           ("metadata", {"image": IMAGE_DATA}), ("pixels", [[0, 1], [2, 3]])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.record_frame(recorder, dict(frame(), **{key: value}))
            with self.subTest(event_key=key), self.assertRaises(ValueError):
                recorder.emit("target", **target(**{key: value}))
        self.record_frame(recorder)
        recorder.close()
        text = recorder.path.read_text(encoding="utf-8")
        self.assertNotIn(IMAGE_DATA, text)
        self.assertEqual([row["type"] for row in read_session(recorder.path).records], ["session", "frame", "end"])

    def test_nested_or_byte_values_cannot_hide_inside_numeric_fields(self):
        recorder = self.recorder(source="web")
        cases = [dict(frame(), raw=[{"image": IMAGE_DATA}, 0]),
                 dict(frame(), gaze_vector=[0, b"PNG", 0]),
                 dict(frame(), origin_m=[0, 0, IMAGE_DATA]),
                 dict(frame(), inference_ms={"image": IMAGE_DATA})]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.record_frame(recorder, data)
        for video in ({"source": "drawImage", "image": IMAGE_DATA},
                      {"source": "drawImage", "mediaTime": {"base64": IMAGE_DATA}},
                      {"source": "drawImage", "mediaTime": b"PNG"}):
            with self.subTest(video=video), self.assertRaises(ValueError):
                self.record_frame(recorder, video_frame=video)
        recorder.close()
        self.assertNotIn(IMAGE_DATA, recorder.path.read_text(encoding="utf-8"))

    def test_metadata_and_reserved_click_schema_have_no_image_escape(self):
        settings = RecordingSettings(self.temporary.name, 8, 3)
        for location in ("root", "screen", "camera"):
            data = deepcopy(metadata())
            if location == "root":
                data["image"] = IMAGE_DATA
            else:
                data[location]["image"] = IMAGE_DATA
            with self.subTest(location=location), self.assertRaises(ValueError):
                SessionRecorder(data, settings=settings)
        recorder = self.recorder()
        with self.assertRaises(ValueError):
            recorder.emit("click", x=0, y=0, button="left", image=IMAGE_DATA)
        with self.assertRaises(ValueError):
            recorder.emit("click", x={"image": IMAGE_DATA}, y=0, button="left")
        self.assertEqual(len(list(Path(self.temporary.name).glob("*.jsonl"))), 1)

    def test_image_data_uri_is_rejected_in_allowed_text_fields(self):
        recorder = self.recorder(source="web")
        for field in ("task_id", "reason"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                recorder.emit("target", **target(**{field: IMAGE_DATA}))
        with self.assertRaises(ValueError):
            recorder.close(reason=IMAGE_DATA)
        data = metadata()
        data["device_name"] = IMAGE_DATA
        with self.assertRaises(ValueError):
            SessionRecorder(data, settings=RecordingSettings(self.temporary.name, 8, 3))

    def test_landmarks_are_discarded_and_never_serialized(self):
        recorder = self.recorder()
        self.assertTrue(self.record_frame(recorder, dict(frame(), landmarks={"image": IMAGE_DATA})))
        recorder.close()
        text = recorder.path.read_text(encoding="utf-8")
        self.assertNotIn("landmarks", text)
        self.assertNotIn(IMAGE_DATA, text)

    def test_each_line_is_complete_flushed_and_end_count_includes_itself(self):
        writer = ObservedOutput()
        recorder = self.recorder(source="web", writer=writer)
        self.assertEqual(json.loads(recorder.path.read_text())["type"], "session")
        self.assertTrue(self.record_frame(recorder))
        writer.wait_for_flushes(2)
        self.assertEqual(len(recorder.path.read_text().splitlines()), 2)
        self.assertTrue(recorder.emit("target", **target()))
        writer.wait_for_flushes(3)
        status = recorder.close(reason="synthetic-test", browser_time_ms=30)
        rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
        self.assertEqual([row["type"] for row in rows], ["session", "frame", "target", "end"])
        self.assertEqual(rows[-1]["written_lines"], len(rows))
        self.assertEqual(rows[-1]["failed_records"], 0)
        self.assertEqual(rows[-1]["reason"], "synthetic-test")
        self.assertEqual(rows[-1]["browser_time_ms"], 30)
        self.assertEqual(status["written_lines"], len(rows))
        self.assertEqual(writer.flush_count, len(rows))
        self.assertEqual([item[0] for item in writer.operations], ["write", "flush"] * len(rows))
        self.assertEqual({item[2] for item in writer.operations}, {"gaze-recorder"})
        self.assertTrue(all(row["page_clock_id"] == "synthetic-page" for row in rows))

    def test_queue_full_stops_recording_without_waiting_for_a_blocked_disk(self):
        writer = ObservedOutput(block_frame=True)
        recorder = self.recorder(writer=writer, queue_size=1)
        self.assertTrue(self.record_frame(recorder))
        self.assertTrue(writer.blocked.wait(3))
        self.assertTrue(self.record_frame(recorder, frame_id=2))
        results = []
        producer = threading.Thread(target=lambda: results.append(self.record_frame(recorder, frame_id=3)))
        producer.start()
        producer.join(.3)
        self.assertFalse(producer.is_alive(), "enqueue blocked on the disk writer")
        self.assertEqual(results, [False])
        status = recorder.status()
        self.assertFalse(status["active"])
        self.assertIn("队列已满", status["error"])
        self.assertEqual(status["failed_records"], 1)
        writer.resume.set()
        recorder.close()
        with self.assertWarnsRegex(ReplayWarning, "会话录制有失败"):
            session = read_session(recorder.path)
        self.assertEqual(session.summary()["frame_count"], 2)
        self.assertEqual(session.records[-1]["reason"], "recording_failed")
        self.assertEqual(session.records[-1]["written_lines"], 4)

    def test_disk_failure_is_visible_and_preserves_previously_flushed_lines(self):
        recorder = self.recorder(writer=ObservedOutput(fail_frame=True))
        self.assertTrue(self.record_frame(recorder))
        self.assertTrue(recorder._closed.wait(3))
        status = recorder.close()
        self.assertFalse(status["active"])
        self.assertIn("synthetic disk full", status["error"])
        self.assertGreater(status["failed_records"], 0)
        self.assertEqual(len(recorder.path.read_text().splitlines()), 1)
        with self.assertWarnsRegex(ReplayWarning, "会话未正常结束"):
            session = read_session(recorder.path)
        self.assertEqual(session.metadata["type"], "session")

    def test_interrupted_last_disk_write_can_be_replayed_with_warning(self):
        recorder = self.recorder(writer=ObservedOutput(truncate_frame=True))
        self.record_frame(recorder)
        self.assertTrue(recorder._closed.wait(3))
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            session = read_session(recorder.path)
        self.assertEqual(len(session.records), 1)
        self.assertTrue(any("截断的最后一行" in str(item.message) for item in notices))
        self.assertTrue(any("会话未正常结束" in str(item.message) for item in notices))

    def test_open_failure_is_reported_to_the_caller(self):
        def fail_open(*args, **kwargs):
            raise OSError("synthetic permission denied")
        with self.assertRaisesRegex(RuntimeError, "synthetic permission denied"):
            self.recorder(writer=fail_open)

    def test_close_is_idempotent_and_writes_only_one_end(self):
        recorder = self.recorder()
        self.record_frame(recorder)
        first = recorder.close(reason="synthetic-first")
        second = recorder.close(reason="synthetic-second")
        self.assertEqual(first, second)
        self.assertFalse(self.record_frame(recorder,frame_id=2))
        recorder.fail('synthetic late producer error')
        self.assertEqual(recorder.status(),first)
        rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
        self.assertEqual(sum(row["type"] == "end" for row in rows), 1)
        self.assertEqual(rows[-1]["reason"], "synthetic-first")


if __name__ == "__main__":
    unittest.main()
