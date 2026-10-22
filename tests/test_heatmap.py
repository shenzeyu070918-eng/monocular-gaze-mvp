"""Synthetic coordinates and fake capture clocks only; no real images."""
from copy import deepcopy
from dataclasses import replace
import unittest
import numpy as np
from gaze_core.config import HeatmapSettings,ScreenGeometry,FixationSettings,load_settings
from gaze_core.heatmap import HeatmapAccumulator,HeatmapStream,RegionStatistics,kernel_scale,rgba_grid

GEOMETRY=ScreenGeometry(width_cm=30,height_cm=20,measured=True,top_cm=0.,focal_scale=1.)
OPTIONS=HeatmapSettings(grid_width=60)
SCALE=kernel_scale(GEOMETRY,(1200,800),2.,28.,'matching_B_check')
IDENTITY=[[1.,0.],[0.,1.],[0.,0.]]


def segment(start,end,center=(.5,.5)):
    return dict(start_s=start,end_s=end,duration_ms=(end-start)*1000,center=list(center))


def context(revision=1,matrix=IDENTITY):
    return dict(matrix=deepcopy(matrix),revision=revision,calibrated=matrix is not None,
                geometry=GEOMETRY,kernel=SCALE,phase=dict(matrix_revision=revision,group='B' if matrix is not None else 'A'))


class HeatmapTests(unittest.TestCase):
    def test_defaults_configuration_and_all_tunable_fields_validate(self):
        options=load_settings(use_local=False).heatmap
        self.assertEqual(options.half_life_s,60);self.assertEqual(options.max_opacity,.4)
        self.assertFalse(options.enabled);self.assertFalse(options.allow_uncalibrated)
        self.assertEqual(replace(options,update_hz=2).update_hz,2)
        for kwargs in (dict(max_opacity=.401),dict(max_opacity=float('nan')),dict(half_life_s=0),dict(grid_width=True),
                       dict(update_hz=6),dict(queue_size=0),dict(allow_uncalibrated=1),
                       dict(regions=[dict(id='a',rect=[0,0,.7,1]),dict(id='b',rect=[.5,0,1,1])]),
                       dict(regions=[dict(id='a',rect=[0,0,1,1]),dict(id='a',rect=[0,0,1,1])])):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):HeatmapSettings(**kwargs)

    def test_kernel_scale_is_sigma_equals_error_radius_in_one_place(self):
        self.assertAlmostEqual(SCALE['sigma_x'],2/30);self.assertAlmostEqual(SCALE['sigma_y'],2/20)
        self.assertEqual(SCALE['sigma_cm'],2.)
        logical=kernel_scale(replace(GEOMETRY,measured=False),(1200,800),999.,28.,'matching_B_check')
        self.assertEqual(logical['sigma_px'],28.);self.assertIsNone(logical['sigma_cm'])
        self.assertAlmostEqual(logical['sigma_x'],28/1200)

    def test_mass_conservation_edges_numpy_and_outside_is_not_clamped(self):
        acc=HeatmapAccumulator(OPTIONS,(1200,800))
        for i,p in enumerate(((0,0),(1,1),(.5,.5))):acc.add(segment(i,i+.75,np.array(p)),SCALE)
        acc.add(segment(3,5,(-.1,.5)),SCALE)
        snap=acc.snapshot(5)
        self.assertAlmostEqual(snap['grid'].sum(),2.25);self.assertAlmostEqual(snap['regions']['total_fixation_s'],2.25)
        self.assertEqual(snap['outside_s'],2);self.assertEqual(snap['fixation_count'],3)
        self.assertEqual(acc.total.shape,(40,60))

    def test_decay_half_life_and_interval_weight_uses_capture_time(self):
        acc=HeatmapAccumulator(OPTIONS,(1200,800),decay=True)
        acc.add(segment(0,2),SCALE)
        first=acc.snapshot(2)['grid'].sum();second=acc.snapshot(62)['grid'].sum()
        self.assertAlmostEqual(second,first/2)
        rate=np.log(2)/60
        self.assertAlmostEqual(first,-np.expm1(-rate*2)/rate)
        self.assertEqual(acc.total.sum(),2.)

    def test_replaced_preview_does_not_double_count_and_matches_final_snapshot(self):
        acc=HeatmapAccumulator(OPTIONS,(1200,800),decay=True)
        for _ in range(12):
            snap=acc.snapshot(1,preview=segment(0,1),scale=SCALE)
            self.assertAlmostEqual(snap['total_grid'].sum(),1.)
            self.assertEqual(snap['regions']['regions'][4]['entries'],1)
        self.assertEqual(acc.total.sum(),0.)
        acc.add(segment(0,1),SCALE);final=acc.snapshot(1)
        np.testing.assert_allclose(snap['grid'],final['grid'])
        self.assertEqual(final['fixation_count'],1)

    def test_region_statistics_hand_calculation_and_interruption(self):
        stats=RegionStatistics([dict(id='left',rect=[0,0,.5,1]),dict(id='right',rect=[.5,0,1,1])])
        for start,end,p in ((1,3,(.1,.5)),(4,5,(.2,.5)),(6,7,(1,1)),(8,9,(.1,.5))):stats.add(segment(start,end,p),0)
        stats.interrupt();stats.add(segment(10,11,(.1,.5)),0)
        rows=stats.snapshot()['regions'];left,right=rows
        self.assertEqual(left['dwell_s'],5);self.assertEqual(left['entries'],3);self.assertEqual(left['fixations'],4)
        self.assertEqual(left['first_fixation_s'],1);self.assertAlmostEqual(left['dwell_fraction'],5/6)
        self.assertEqual(right['dwell_s'],1);self.assertEqual(right['entries'],1)

    def test_stream_invalid_and_saccade_frames_do_not_gain_time(self):
        stream=HeatmapStream(OPTIONS,FixationSettings(),(1200,800),decay=False)
        stream.configure(context())
        def feed(t,p=(.2,.2),valid=True,status='tracking'):
            stream.feed(dict(raw=p,valid=valid,status=status,matrix_revision=1),round(t*1e9))
        for t in (0,.07,.14,.21):feed(t)
        feed(.28,None,False,'blink')
        for t in (.35,.42,.49,.56):feed(t)
        feed(.63,(.9,.9))
        stream.interrupt();snap=stream.snapshot(640_000_000)
        self.assertAlmostEqual(snap['grid'].sum(),.42)
        self.assertEqual(snap['fixation_count'],2);self.assertEqual(snap['skipped']['blink'],1)
        self.assertEqual(snap['fallback_frames'],9)

    def test_stream_calibration_switch_closes_old_segment_and_uses_new_matrix(self):
        stream=HeatmapStream(OPTIONS,FixationSettings(),(1200,800),decay=False)
        stream.configure(context())
        for t in (0,.07,.14):stream.feed(dict(raw=(.2,.2),valid=True,status='tracking',matrix_revision=1),round(t*1e9))
        shifted=deepcopy(IDENTITY);shifted[2]=[.5,.5];stream.configure(context(2,shifted))
        stream.feed(dict(raw=(.2,.2),valid=True,status='tracking',matrix_revision=1),210_000_000)
        for t in (.28,.35,.42):stream.feed(dict(raw=(.2,.2),valid=True,status='tracking',matrix_revision=2),round(t*1e9))
        snap=stream.snapshot(430_000_000)
        self.assertAlmostEqual(snap['total_grid'].sum(),.28)
        self.assertEqual(snap['skipped']['matrix_revision_mismatch'],1)
        self.assertEqual(snap['phases'][0]['context']['matrix_revision'],1)

    def test_missing_calibration_gap_and_stale_preview_do_not_invent_dwell(self):
        stream=HeatmapStream(OPTIONS,FixationSettings(),(1200,800),decay=False)
        stream.configure(context(matrix=None))
        stream.feed(dict(raw=(.5,.5),valid=True,status='tracking',matrix_revision=1),0)
        self.assertEqual(stream.skipped['uncalibrated'],1)
        stream.configure(context())
        for t in (.1,.17,.24):stream.feed(dict(raw=(.5,.5),valid=True,status='tracking',matrix_revision=1),round(t*1e9))
        snap=stream.snapshot(9_000_000_000)
        self.assertAlmostEqual(snap['total_grid'].sum(),.14)
        self.assertFalse(snap['ongoing_fixation'])

    def test_alpha_ceiling_and_decay_fades_instead_of_peak_renormalization(self):
        grid=np.ones((5,5))*.01
        first=rgba_grid(grid,.4);later=rgba_grid(grid/2,.4)
        self.assertLessEqual(int(first[...,3].max()),102)
        self.assertTrue(np.all(later[...,3]<first[...,3]))
        self.assertTrue(np.all(rgba_grid(grid*0,.4)[...,3]==0))


if __name__=='__main__':unittest.main()
