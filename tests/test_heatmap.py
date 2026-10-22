"""Synthetic coordinates and fake capture clocks only; no real images."""
from copy import deepcopy
from dataclasses import replace
import unittest
import numpy as np
from gaze_core.config import HeatmapSettings,ScreenGeometry,FixationSettings,load_settings
from gaze_core.heatmap import HeatmapAccumulator,HeatmapStream,RegionStatistics,kernel_scale,rgba_grid,heatmap_position,kernel_description

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
        self.assertEqual(options.display_mode,'on_demand');self.assertEqual(options.view_update_hz,1.)
        self.assertEqual(options.alpha_threshold,.15)
        self.assertEqual(replace(options,update_hz=2).update_hz,2)
        for kwargs in (dict(max_opacity=.401),dict(max_opacity=float('nan')),dict(half_life_s=0),dict(grid_width=True),
                       dict(update_hz=6),dict(view_update_hz=0),dict(view_update_hz=6),dict(display_mode='other'),
                       dict(alpha_threshold=-.1),dict(alpha_threshold=1),dict(alpha_threshold=True),dict(alpha_threshold=float('nan')),
                       dict(queue_size=0),dict(allow_uncalibrated=1),
                       dict(regions=[dict(id='a',rect=[0,0,.7,1]),dict(id='b',rect=[.5,0,1,1])]),
                       dict(regions=[dict(id='a',rect=[0,0,1,1]),dict(id='a',rect=[0,0,1,1])])):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):HeatmapSettings(**kwargs)

    def test_kernel_scale_is_sigma_equals_error_radius_in_one_place(self):
        self.assertAlmostEqual(SCALE['sigma_x'],2/30);self.assertAlmostEqual(SCALE['sigma_y'],2/20)
        self.assertEqual(SCALE['sigma_cm'],2.)
        logical=kernel_scale(replace(GEOMETRY,measured=False),(1200,800),999.,28.,'matching_B_check')
        self.assertEqual(logical['sigma_px'],28.);self.assertIsNone(logical['sigma_cm'])
        self.assertAlmostEqual(logical['sigma_x'],28/1200)
        self.assertIn('2.000 cm',kernel_description(SCALE));self.assertIn('最近匹配 B 核对',kernel_description(SCALE))
        self.assertIn('28.000 逻辑像素',kernel_description(logical));self.assertIn('配置示意值',kernel_description(logical))
        self.assertIn('等待',kernel_description(None))

    def test_mass_conservation_edges_numpy_and_far_outside_is_not_clamped(self):
        acc=HeatmapAccumulator(OPTIONS,(1200,800))
        for i,p in enumerate(((0,0),(1,1),(.5,.5))):acc.add(segment(i,i+.75,np.array(p)),SCALE)
        acc.add(segment(3,5,(-.1,.5)),SCALE)
        snap=acc.snapshot(5)
        self.assertAlmostEqual(snap['grid'].sum(),2.25);self.assertAlmostEqual(snap['regions']['total_fixation_s'],2.25)
        self.assertEqual(snap['outside_s'],2);self.assertEqual(snap['fixation_count'],3)
        self.assertEqual(acc.total.shape,(40,60))

    def test_shared_edge_projection_sigma_boundary_corners_numpy_and_logical_units(self):
        for point,expected in (([-SCALE['sigma_x'],.5],[0.,.5]),([1+SCALE['sigma_x'],.5],[1.,.5]),
                               ([.5,-SCALE['sigma_y']],[.5,0.]),([.5,1+SCALE['sigma_y']],[.5,1.]),
                               ([-.6*SCALE['sigma_x'],-.6*SCALE['sigma_y']],[0.,0.])):
            with self.subTest(point=point):
                raw=np.array(point,dtype=np.float64);original=raw.copy()
                projected,status=heatmap_position(raw,SCALE)
                self.assertEqual(projected,expected);self.assertEqual(status,'edge_clamped')
                np.testing.assert_array_equal(raw,original)
        for point in ((-1.01*SCALE['sigma_x'],.5),(-.8*SCALE['sigma_x'],-.8*SCALE['sigma_y'])):
            self.assertEqual(heatmap_position(point,SCALE),(None,'outside'))
        self.assertEqual(heatmap_position([1.,0.],SCALE),([1.,0.],'inside'))
        logical=kernel_scale(replace(GEOMETRY,measured=False),(1200,800),999.,28.,'configured_cm')
        self.assertEqual(heatmap_position([.5,-28/800],logical),([.5,0.],'edge_clamped'))
        self.assertEqual(heatmap_position([.5,-29/800],logical),(None,'outside'))

    def test_clamped_duration_counts_in_mass_and_regions_but_far_outside_remains_separate(self):
        acc=HeatmapAccumulator(OPTIONS,(1200,800))
        for start,end,point in ((0,1,(.5,-.05)),(1,3,(1.03,.5)),(3,6,(.5,.5)),(6,10,(-.1,.5))):
            acc.add(segment(start,end,point),SCALE)
        snap=acc.snapshot(10);rows={row['id']:row for row in snap['regions']['regions']}
        self.assertAlmostEqual(snap['grid'].sum(),6.)
        self.assertEqual(snap['edge_clamped_s'],3.);self.assertEqual(snap['outside_s'],4.)
        self.assertEqual(snap['regions']['total_fixation_s'],6.)
        self.assertEqual(rows['1-2']['dwell_s'],1.);self.assertEqual(rows['2-3']['dwell_s'],2.)
        np.testing.assert_array_equal(acc.kernel([.5,-.05],SCALE),acc.kernel([.5,0.],SCALE))
        self.assertEqual(snap['regions']['total_fixation_s']+snap['outside_s'],10.)

    def test_clamped_preview_replacement_and_decay_do_not_double_count_duration(self):
        acc=HeatmapAccumulator(OPTIONS,(1200,800),decay=True)
        preview=segment(0,1,(.5,-.05))
        for _ in range(10):
            snap=acc.snapshot(1,preview=preview,scale=SCALE)
            self.assertEqual(snap['edge_clamped_s'],1.);self.assertEqual(snap['outside_s'],0.)
            self.assertEqual(snap['regions']['total_fixation_s'],1.)
        self.assertEqual(acc.edge_clamped_s,0.)
        acc.add(preview,SCALE);first=acc.snapshot(1);later=acc.snapshot(61)
        self.assertEqual(later['edge_clamped_s'],1.)
        np.testing.assert_allclose(snap['grid'],first['grid'])
        self.assertAlmostEqual(later['grid'].sum(),first['grid'].sum()/2)
        far=acc.snapshot(61,preview=segment(60,61,(.5,-.2)),scale=SCALE)
        self.assertEqual(far['outside_s'],1.);self.assertEqual(far['edge_clamped_s'],1.)

    def test_live_stream_projects_only_after_detection_preserving_raw_outside_position(self):
        stream=HeatmapStream(OPTIONS,FixationSettings(),(1200,800),decay=False)
        stream.configure(context());raw=[np.float64(-.03),np.float64(.5)]
        data=dict(raw=raw,valid=True,status='tracking',matrix_revision=1)
        for t in (0,.07,.14):stream.feed(data,round(t*1e9))
        self.assertAlmostEqual(stream.preview['center'][0],-.03)
        snap=stream.snapshot(150_000_000)
        self.assertAlmostEqual(snap['edge_clamped_s'],.14);self.assertEqual(snap['outside_s'],0.)
        self.assertEqual(data['raw'],raw);self.assertEqual(raw[0],-.03)
        stream.interrupt();final=stream.snapshot(160_000_000)
        self.assertAlmostEqual(final['edge_clamped_s'],.14)
        self.assertAlmostEqual(final['regions']['total_fixation_s'],.14)

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

    def test_relative_alpha_threshold_warm_colors_and_ceiling_leave_numeric_data_unchanged(self):
        grid=np.array([[0,.10,.149,.15,.2,.5,1.]],dtype=np.float64);before=grid.copy()
        colors=rgba_grid(grid,.4,.15)
        self.assertTrue(np.all(colors[0,:4,3]==0))
        self.assertTrue(np.all(np.diff(colors[0,:,3].astype(int))>=0))
        self.assertEqual(int(colors[...,3].max()),102)
        self.assertTrue(np.all(colors[...,0]==255));self.assertTrue(np.all(colors[...,2]==0))
        np.testing.assert_array_equal(colors[0,-1],[255,0,0,102])
        self.assertGreater(int(colors[0,4,1]),200)  # yellow, no dark/cool low end
        np.testing.assert_array_equal(rgba_grid(grid/2,.4,.15),colors)
        np.testing.assert_array_equal(grid,before)
        self.assertTrue(np.all(rgba_grid(grid*0,.4,.15)[...,3]==0))
        self.assertTrue(np.all(rgba_grid(grid,.4,.6)[0,:6,3]==0))


if __name__=='__main__':unittest.main()
