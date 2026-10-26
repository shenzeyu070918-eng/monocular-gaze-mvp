"""Synthetic capture clocks/NumPy only; no camera, Qt or browser."""
from dataclasses import replace
import unittest
import numpy as np
from gaze_core.galaxy import GalaxySettings,PatternCycle
from gaze_core.galaxy_scene import LEAVE,GAP,COOLDOWN,INVALID


class CornerTests(unittest.TestCase):
    def make(self):return PatternCycle(GalaxySettings())
    def feed(self,cycle,ms,point=(-15,-10),valid=True,radii=(30,25)):
        return cycle.feed(np.asarray(point),np.asarray(radii),round(ms*1e6),valid,viewport_size=(1000,800))

    def test_small_outside_clamps_for_hits_without_mutating_raw_numpy_point(self):
        c=self.make();point=np.array([-50.,-30.]);before=point.copy()
        for t in range(0,1501,100):self.feed(c,t,point)
        np.testing.assert_array_equal(point,before)
        self.assertEqual(c.pattern,1);self.assertEqual(c.snapshot()['corner_progress'],1)
        self.assertEqual(c.snapshot()['corner'],0)
        # Radius may be larger than ratio-distance; use the larger tolerance.
        c=self.make()
        for t in range(0,1501,100):self.feed(c,t,(-250,10),radii=(300,25))
        self.assertEqual(c.pattern,1)

    def test_jitter_throughout_same_region_does_not_restart_anchor(self):
        c=self.make()
        for i,t in enumerate(range(0,1501,100)):self.feed(c,t,((20,20),(195,155),(-30,50))[i%3])
        self.assertEqual(c.pattern,1)

    def test_leave_grace_freezes_progress_and_expires(self):
        c=self.make()
        for t in range(0,701,100):self.feed(c,t)
        held=c.snapshot()['corner_dwell_ms']
        self.feed(c,800,(500,400));self.assertEqual(c.snapshot()['corner_state'],2)
        self.feed(c,1000,(500,400));self.assertEqual(c.snapshot()['corner_dwell_ms'],held)
        self.feed(c,1100);self.assertEqual(c.snapshot()['corner_dwell_ms'],held)  # Boundary is within grace.
        self.feed(c,1200,(500,400));self.feed(c,1501,(500,400))
        self.assertEqual(c.snapshot()['corner_progress'],0);self.assertEqual(c.snapshot()['corner_reset'],LEAVE)
        self.feed(c,1600);self.assertEqual(c.snapshot()['corner_dwell_ms'],0)

    def test_blink_and_single_no_face_freeze_instead_of_reset_or_counting_as_gaze(self):
        c=self.make()
        for t in range(0,801,100):self.feed(c,t)
        for t in (900,1000):self.feed(c,t,valid=False)
        self.feed(c,1100);self.assertEqual(c.snapshot()['corner_dwell_ms'],800)
        for t in range(1200,1801,100):self.feed(c,t)
        self.assertEqual(c.pattern,1)
        c=self.make();self.feed(c,0);self.feed(c,100,valid=False);self.feed(c,401,valid=False)
        self.assertEqual(c.snapshot()['corner_reset'],INVALID)

    def test_far_outside_different_corner_gap_and_duplicates(self):
        c=self.make();self.feed(c,0);self.feed(c,500)
        self.assertEqual(c.snapshot()['corner_reset'],GAP);self.assertEqual(c.snapshot()['corner_dwell_ms'],0)
        self.feed(c,600);self.feed(c,600);self.assertEqual(c.snapshot()['corner_dwell_ms'],100)
        self.feed(c,700,(-250,-200));self.feed(c,1001,(-250,-200))
        self.assertEqual(c.snapshot()['corner'], -1)
        self.feed(c,1100,(990,790));self.assertEqual(c.snapshot()['corner'],2)
        self.assertEqual(c.snapshot()['corner_dwell_ms'],0)

    def test_different_regions_never_share_accumulated_dwell(self):
        c=self.make()
        for t in range(0,801,100):self.feed(c,t)
        self.feed(c,900,(990,10));self.feed(c,1100)
        self.assertEqual(c.snapshot()['corner'],0);self.assertEqual(c.snapshot()['corner_dwell_ms'],800)
        self.feed(c,1200,(990,10));self.feed(c,1501,(990,10))
        self.assertEqual(c.snapshot()['corner'],1);self.assertEqual(c.snapshot()['corner_dwell_ms'],0)

    def test_zero_grace_is_an_immediate_leave(self):
        o=GalaxySettings();c=PatternCycle(replace(o,interaction=replace(o.interaction,corner_leave_grace_ms=0)))
        self.feed(c,0);self.feed(c,100);self.feed(c,200,(500,400))
        self.assertEqual(c.snapshot()['corner'],-1);self.assertEqual(c.snapshot()['corner_progress'],0)

    def test_cooldown_and_keyboard_steps_share_same_cycle(self):
        c=self.make();status=c.advance(1,0);self.assertEqual(status['pattern'],1)
        self.assertEqual(status['scene_revision'],1);self.assertEqual(status['corner_reset'],COOLDOWN)
        for t in range(0,1200,100):self.feed(c,t)
        self.assertEqual(c.pattern,1)
        for t in range(1200,2701,100):self.feed(c,t)
        self.assertEqual(c.pattern,2)
        self.assertEqual(c.advance(-1,2_800_000_000)['pattern'],1)
        self.assertEqual(c.advance(1,2_900_000_000)['pattern'],2)
        self.assertEqual(c.advance(1,3_000_000_000)['pattern'],0)
        for invalid in (0,2,True):
            with self.assertRaises(ValueError):c.advance(invalid,4_000_000_000)


if __name__=='__main__':unittest.main()
