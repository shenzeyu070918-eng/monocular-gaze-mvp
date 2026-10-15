"""Deterministically order stale session cleanup; no cameras, models, or disk."""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gaze_core.web_recording import RecordingConflict, WebRecording


class FakeRecorder:
    def __init__(self, metadata, session_id):
        self.session_id = session_id
        self.page_clock_id = metadata["page_clock_id"]
        self.error = None
        self.closed = False
        self.frames = []
        self.end_count = 0

    def status(self):
        return {"session_id": self.session_id, "active": not self.closed and self.error is None,
                "error": self.error, "file": f"{self.session_id}.jsonl"}

    def record_frame(self, data, **timing):
        if self.closed or self.error:
            return False
        self.frames.append((data, timing))
        return True

    def fail(self, message):
        self.error = message

    def close(self, reason="user", browser_time_ms=None):
        if not self.closed:
            self.end_count += 1
        self.closed = True
        return self.status()


class OrderedLifecycle:
    """A real lock with an optional gate just before one named caller acquires it."""
    def __init__(self, blocked_thread=None):
        self.lock = threading.Lock()
        self.blocked_thread = blocked_thread
        self.release_gate = threading.Event()
        self.attempts = {name: threading.Event() for name in
                         ("old-stop-a", "old-stop-b", "new-start", "gaze-web-recording-close")}

    def __enter__(self):
        name = threading.current_thread().name
        if name in self.attempts:
            self.attempts[name].set()
        if name == self.blocked_thread and not self.release_gate.wait(3):
            raise RuntimeError("synthetic lifecycle gate timed out")
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class WebRecordingRaceTests(unittest.TestCase):
    def setUp(self):
        metadata = patch("gaze_core.web_recording.session_metadata",
                         side_effect=lambda **fields: {"page_clock_id": fields["page_clock_id"]})
        metadata.start()
        self.addCleanup(metadata.stop)
        self.recorders = []
        def factory(metadata, **kwargs):
            recorder = FakeRecorder(metadata, f"synthetic-{len(self.recorders)}")
            self.recorders.append(recorder)
            return recorder
        settings = SimpleNamespace(recording=SimpleNamespace(queue_size=4, close_timeout_s=3))
        self.manager = WebRecording(settings, recorder_factory=factory)
        self.addCleanup(self.manager.stop)
        self.results, self.errors, self.threads = {}, {}, []

    def start(self, clock="old-page"):
        return self.manager.start(engine=None, geometry=None, screen={}, measured=False,
                                  grayscale=False, camera_size=(640, 480), page_clock_id=clock)

    def spawn(self, name, operation):
        def run():
            try:
                self.results[name] = operation()
            except Exception as error:
                self.errors[name] = error
        thread = threading.Thread(target=run, name=name)
        self.threads.append(thread)
        thread.start()
        return thread

    def join(self, thread):
        thread.join(3)
        self.assertFalse(thread.is_alive(), f"{thread.name} did not finish")

    def inflight(self, session_id):
        return self.manager.begin_frame(session_id, "old-page", 1,
                                        browser_capture_ms=1, browser_request_ms=2,
                                        request_received_ns=100)

    def complete(self, ticket):
        with self.manager._condition:
            if ticket[1] in self.manager._inflight:
                self.manager.complete_frame(ticket, {"valid": False, "status": "no_face", "inference_ms": 1}, 200)

    def watch_first_wait(self):
        waiting = threading.Event()
        wait_for = self.manager._condition.wait_for
        def observed(predicate, timeout=None):
            if threading.current_thread().name == "old-stop-a":
                waiting.set()
            return wait_for(predicate, timeout)
        self.manager._condition.wait_for = observed
        return waiting

    def test_stale_second_stop_cannot_close_a_new_session(self):
        old_id = self.start()["session_id"]
        ticket = self.inflight(old_id)
        lifecycle = OrderedLifecycle(blocked_thread="old-stop-b")
        self.manager._lifecycle = lifecycle
        self.addCleanup(lifecycle.release_gate.set)
        waiting = self.watch_first_wait()
        try:
            first = self.spawn("old-stop-a", lambda: self.manager.stop(old_id, "old-stop", 10))
            self.assertTrue(waiting.wait(3))
            second = self.spawn("old-stop-b", lambda: self.manager.stop(old_id, "old-stop", 10))
            self.assertTrue(lifecycle.attempts["old-stop-b"].wait(3))
            starter = self.spawn("new-start", lambda: self.start("new-page"))
            self.assertTrue(lifecycle.attempts["new-start"].wait(3))
            self.assertNotIn("new-start", self.results)
            # Frame completion must remain possible while lifecycle operations wait.
            self.complete(ticket)
            self.join(first)
            self.join(starter)
            new_id = self.results["new-start"]["session_id"]
            self.assertNotEqual(old_id, new_id)
            lifecycle.release_gate.set()
            self.join(second)
            self.assertIsInstance(self.errors.get("old-stop-b"), RecordingConflict)
            self.assertEqual(set(self.errors), {"old-stop-b"})
            self.assertEqual(self.results["old-stop-a"]["session_id"], old_id)
            self.assertTrue(self.manager.status()["active"])
            self.assertEqual(self.manager.status()["session_id"], new_id)
            self.assertEqual(self.recorders[-1].end_count, 0)
            self.assertEqual(len(self.recorders[0].frames), 1)
        finally:
            lifecycle.release_gate.set()
            self.complete(ticket)
            for thread in self.threads:
                thread.join(3)

    def test_stale_failure_cleanup_cannot_poison_the_replacement_session(self):
        old_id = self.start()["session_id"]
        lifecycle = OrderedLifecycle(blocked_thread="gaze-web-recording-close")
        self.manager._lifecycle = lifecycle
        self.addCleanup(lifecycle.release_gate.set)
        # A failed writer is inactive before its asynchronous cleanup gets the lock.
        self.recorders[0].fail("synthetic old disk failure")
        with self.manager._condition:
            self.manager._signal_failure("synthetic old disk failure")
        cleanup = self.manager._failure_cleanup
        try:
            self.assertTrue(lifecycle.attempts["gaze-web-recording-close"].wait(3))
            new_id = self.start("new-page")["session_id"]
            self.assertNotEqual(old_id, new_id)
            lifecycle.release_gate.set()
            self.join(cleanup)
            self.assertIsNone(self.manager.status()["error"])
            self.assertTrue(self.manager.status()["active"])
            self.assertEqual(self.manager.status()["session_id"], new_id)
            self.assertEqual(self.recorders[-1].end_count, 0)
        finally:
            lifecycle.release_gate.set()
            cleanup.join(3)

    def test_two_normal_stops_wait_for_one_frame_and_end_the_session_once(self):
        old_id = self.start()["session_id"]
        ticket = self.inflight(old_id)
        lifecycle = OrderedLifecycle()
        self.manager._lifecycle = lifecycle
        waiting = self.watch_first_wait()
        try:
            first = self.spawn("old-stop-a", lambda: self.manager.stop(old_id, "first", 10))
            self.assertTrue(waiting.wait(3))
            second = self.spawn("old-stop-b", lambda: self.manager.stop(old_id, "second", 11))
            self.assertTrue(lifecycle.attempts["old-stop-b"].wait(3))
            self.complete(ticket)
            self.join(first)
            self.join(second)
            self.assertEqual(self.errors, {})
            self.assertEqual([result["session_id"] for result in self.results.values()], [old_id, old_id])
            self.assertEqual(self.recorders[0].end_count, 1)
            self.assertEqual(len(self.recorders[0].frames), 1)
            self.assertFalse(self.manager.status()["active"])
        finally:
            self.complete(ticket)
            for thread in self.threads:
                thread.join(3)


if __name__ == "__main__":
    unittest.main()
