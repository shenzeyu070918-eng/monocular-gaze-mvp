"""Synthetic capture times only; no real faces or input hooks."""
import unittest
from dataclasses import replace
import numpy as np
from gaze_core.galaxy import GalaxySettings
from gaze_core.galaxy_burst import ClosedEyeBurst,SHORT,NO_FACE,GAP,COOLDOWN,BLOCKED
from gaze_core.galaxy_scene import PatternCycle


class BurstTests(unittest.TestCase):
    def make(self):return ClosedEyeBurst(GalaxySettings().interaction)
    def feed(self,c,ms,status='blink',**kw):c.feed(np.int64(ms*1_000_000),status,**kw)
    def test_natural_blinks_do_not_fire(self):
        for duration in (100,200,299,500):
            c=self.make();self.feed(c,0,'tracking')
            for ms in range(100,100+duration,50):self.feed(c,ms)
            self.feed(c,100+duration,'tracking');self.assertEqual(c.revision,0);self.assertEqual(c.reason,SHORT)
    def test_observed_600ms_closed_fires_once_only_on_reopening(self):
        c=self.make();self.feed(c,0,'tracking')
        for ms in range(100,701,100):self.feed(c,ms)
        self.assertEqual(c.snapshot(700_000_000)['blink_closed_ms'],600);self.assertEqual(c.revision,0)
        self.feed(c,800,'tracking');self.assertEqual(c.revision,1);self.assertEqual(c.blink_count,1)
        self.assertEqual(c.snapshot(800_000_000)['burst_active'],1)
        self.feed(c,800,'tracking');self.assertEqual(c.revision,1)
        self.assertEqual(c.snapshot(3_300_000_000)['burst_active'],0)
    def test_no_face_and_gaps_cancel_entire_closed_cycle_until_open(self):
        for bad,reason in (('no_face',NO_FACE),('gap',GAP)):
            c=self.make();self.feed(c,0,'tracking');self.feed(c,100)
            if bad=='gap':self.feed(c,600)
            else:self.feed(c,200,bad)
            self.assertEqual(c.reason,reason)
            for ms in range(700,1501,100):self.feed(c,ms)
            self.feed(c,1600,'tracking');self.assertEqual(c.revision,0)
    def test_cooldown_intro_transition_and_unknown_status(self):
        c=self.make();self.assertTrue(c.trigger(0))
        for ms in range(100,1001,100):self.feed(c,ms)
        self.feed(c,1100,'tracking');self.assertEqual(c.revision,1);self.assertEqual(c.reason,COOLDOWN)
        self.assertFalse(c.trigger(2_000_000_000));self.assertTrue(c.trigger(3_500_000_000))
        self.feed(c,3600,'tracking',enabled=False);self.assertEqual(c.reason,BLOCKED)
        self.assertEqual(c.snapshot(3_600_000_000)['burst_active'],0)
        c=self.make();self.feed(c,0,'tracking');self.feed(c,100);self.feed(c,200,'engine_error')
        for ms in range(300,1001,100):self.feed(c,ms)
        self.feed(c,1100,'tracking');self.assertEqual(c.revision,0)
    def test_open_eye_classification_does_not_require_valid_gaze_geometry(self):
        c=self.make();self.feed(c,0,'tracking')
        for ms in range(100,701,100):self.feed(c,ms)
        self.feed(c,800,'geometry_invalid');self.assertEqual(c.blink_count,1)

    def test_starting_with_closed_eyes_and_duplicates_never_proves_charge(self):
        c=self.make()
        for ms in range(0,1001,100):self.feed(c,ms)
        self.feed(c,1100,'tracking');self.assertEqual(c.revision,0)
        self.feed(c,1200)
        for _ in range(1000):self.feed(c,1200)
        self.feed(c,1250,'tracking');self.assertEqual(c.revision,0)
    def test_options_reject_unsafe_or_empty_envelopes(self):
        for values in ({'blink_hold_ms':200},{'burst_cooldown_ms':1000},{'burst_duration_ms':500}):
            with self.assertRaises(ValueError):replace(GalaxySettings().interaction,**values)


class AssemblyOwnerTests(unittest.TestCase):
    def test_requires_matching_browser_ack_after_full_duration(self):
        c=PatternCycle(GalaxySettings());s=c.advance(1,0)
        self.assertEqual((s['scene_from'],s['pattern'],s['scene_transition']),(0,1,1))
        self.assertFalse(c.complete(1,2_000_000_000));self.assertFalse(c.complete(0,3_000_000_000))
        c.advance(1,4_000_000_000);self.assertEqual(c.pattern,1) # slow/hidden browser stays locked
        self.assertTrue(c.complete(1,4_000_000_000));self.assertEqual(c.snapshot(4_000_000_000)['scene_transition'],0)
        self.assertEqual(c.advance(1,4_100_000_000)['pattern'],2)


if __name__=='__main__':unittest.main()
