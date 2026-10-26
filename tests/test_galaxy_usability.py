"""Synthetic capture times, viewport geometry and M4 samples; no hardware."""
from dataclasses import replace
import unittest
import numpy as np
from gaze_core.config import ScreenGeometry
from gaze_core.galaxy import GalaxySettings,PatternCycle
from gaze_core.galaxy_entry import EntryRefresh,BrowserCaptureClock,REASONS
from gaze_core.galaxy_protocol import entry_request,viewport
from overlay.mapping import ScreenRect
from test_galaxy import geometry

class EdgeTests(unittest.TestCase):
    def test_full_height_outside_jitter_direction_and_cooldown(self):
        for x,expected in ((-180,2),(1180,1)):
            c=PatternCycle(GalaxySettings())
            for n,ms in enumerate(range(0,1501,100)):
                c.feed([x if n%2 else max(5,min(995,x)),20+n*45],[30,30],ms*1_000_000,viewport_size=(1000,800))
            self.assertEqual(c.pattern,expected);self.assertEqual(c.snapshot()['switch_mode'],1)
            for ms in range(1600,5000,100):c.feed([x,400],[30,30],ms*1_000_000,viewport_size=(1000,800))
            self.assertEqual(c.revision,1)
    def test_grace_does_not_share_evidence_between_edges(self):
        c=PatternCycle(GalaxySettings())
        def f(t,p,valid=True):c.feed(p,[30,30],t*1_000_000,valid,viewport_size=(1000,800))
        for t in range(0,701,100):f(t,[20,400])
        f(800,[500,400]);f(1000,[20,400]);self.assertEqual(c.snapshot()['corner_dwell_ms'],700)
        f(1100,[980,400]);f(1401,[980,400]);self.assertEqual(c.snapshot()['corner_dwell_ms'],0)
        self.assertEqual(c.snapshot()['corner'],1)
    def test_corner_opt_in_and_off(self):
        o=GalaxySettings()
        for mode in ('corners','off'):
            c=PatternCycle(replace(o,interaction=replace(o.interaction,scene_switch_mode=mode)))
            for t in range(0,1601,100):c.feed([5,400],[30,30],t*1_000_000,viewport_size=(1000,800))
            self.assertEqual(c.pattern,0)

class EntryTests(unittest.TestCase):
    geometry=ScreenGeometry(30.,20.,1.,1.,measured=True)
    def make(self,corrected=(.48,.49),raw=(.3,.6)):
        e=EntryRefresh(GalaxySettings().interaction,self.geometry,(.01,.01))
        for ms in range(0,1001,50):
            e.add(ms*1_000_000,dict(valid=True,status='tracking',raw=np.array(raw),head_degrees=np.zeros(3),display=dict(unfiltered=corrected)))
        return e
    def test_m4_selection_then_median_of_calibrated_unfiltered_samples(self):
        e=self.make();r=e.apply(1_000_000_000,(.5,.5));self.assertEqual(r['applied'],1);self.assertEqual(r['samples'],7)
        np.testing.assert_allclose(e.offset,[.02,.01]);np.testing.assert_allclose(r['offset_cm'],[.6,.2])
        self.assertEqual(set(r),{'applied','offset_cm','used_offset_cm','samples','reason'})
    def test_large_nan_unstable_and_insufficient_samples_preserve_original_offset(self):
        cases=[(self.make((.1,.1)),1_000_000_000),(self.make(raw=(np.nan,.6)),1_000_000_000),(self.make(),10)]
        for e,t in cases:
            self.assertFalse(e.apply(t,(.5,.5))['applied']);self.assertEqual(e.offset,(.01,.01))
        self.assertEqual(cases[0][0].reports[-1]['reason'],REASONS['excessive_offset'])
    def test_never_uses_post_click_samples_or_writes_raw_positions_in_report(self):
        e=self.make();e.add(1_050_000_000,dict(valid=True,raw=[.9,.9],head_degrees=[0,0,0],display=dict(unfiltered=[.9,.9])))
        self.assertEqual(e.apply(1_000_000_000,(.5,.5))['samples'],7);np.testing.assert_allclose(e.offset,[.02,.01])
        self.assertNotIn('raw',e.reports[0]);self.assertNotIn('target',e.reports[0])
    def test_no_clock_or_measured_geometry_or_corrected_signal_keeps_practice_offset(self):
        e=self.make();self.assertEqual(e.apply(None,(.5,.5))['reason'],REASONS['missing_clock'])
        e=self.make(None);self.assertEqual(e.apply(1_000_000_000,(.5,.5))['reason'],REASONS['missing_corrected_position'])
        e.geometry=replace(self.geometry,measured=False);self.assertFalse(e.apply(1_000_000_000,(.5,.5))['applied'])
    def test_notch_button_center_and_entry_whitelist(self):
        m=viewport(ScreenRect(0,0,1512,982,2),geometry());r=entry_request(dict(buttonX=756,buttonY=475,click_ms=1000),m)
        np.testing.assert_allclose(m.window_to_screen((r['buttonX'],r['buttonY'])),[756/1511,507/981])
        for changes in ({'click_ms':float('nan')},{'buttonX':2000},{'image':0}):
            with self.assertRaises(ValueError):entry_request(dict(buttonX=756,buttonY=475,click_ms=1000,**changes) if 'image' in changes else {**dict(buttonX=756,buttonY=475,click_ms=1000),**changes},m)
    def test_midpoint_clock_alignment_and_uncertainty_guard(self):
        c=BrowserCaptureClock(100);i=c.probe(1_000_000_000)
        self.assertFalse(c.reply(i+1,500.,1_010_000_000));self.assertTrue(c.reply(i,500.,1_020_000_000))
        self.assertEqual(c.capture_time(490.,1_040_000_000,300),1_000_000_000)
        self.assertIsNone(c.capture_time(900.,1_040_000_000,300))
        c=BrowserCaptureClock(100);i=c.probe(0);self.assertFalse(c.reply(i,10,101_000_000));self.assertIsNone(c.capture_time(10,200_000_000,300))

if __name__=='__main__':unittest.main()
