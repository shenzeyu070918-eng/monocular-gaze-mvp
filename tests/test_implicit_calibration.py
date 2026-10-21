"""Synthetic numeric data only: none of these errors are human measurements."""
from copy import deepcopy
from dataclasses import replace
import unittest
import numpy as np

from gaze_core.config import ScreenGeometry
from gaze_core.implicit_calibration import (ExperimentSettings,load_experiment_settings,sample_clicks,
    deduplicate_clicks,fit_robust,coverage,decide_m5,blocked_cv)


GEOMETRY=ScreenGeometry(30.,20.,0.,1.,measured=True)


def small_settings(**kwargs):
    values=dict(windows_ms=((500.,0.),(500.,100.)),models=('offset','scale_offset','affine'),counts=(5,10),
                recent_minutes=(1.,3.),ransac_iterations=30,repeats=3,cv_folds=2,min_occupied_cells=2)
    values.update(kwargs);return ExperimentSettings(**values)


def stable_click(number=0,*,target=(.3,.4),raw=(.2,.25)):
    t=int(2e9+number*2e9)
    rows=[dict(capture_ns=t-int(dt*1e6),raw=[np.float64(raw[0]),np.float32(raw[1])],valid=True,status='tracking',
               head_degrees=[np.float64(0),0,0],origin_m=[0,0,.5],frame_id=number*10+i)
          for i,dt in enumerate(range(450,-1,-50))]
    return rows,dict(event_ns=t,x=target[0],y=target[1],button='left',excluded=False)


class ImplicitCalibrationTests(unittest.TestCase):
    def test_config_loads_exploratory_not_conclusion(self):
        self.assertGreater(len(load_experiment_settings().windows_ms),1)
        for kwargs in ({'windows_ms':[(100,200)]},{'min_valid_ratio':2},{'models':['quadratic']},
                       {'seed':True},{'duplicate_ms':0},{'cv_folds':1}):
            with self.subTest(kwargs=kwargs),self.assertRaises((ValueError,TypeError)):ExperimentSettings(**kwargs)

    def test_raw_numpy_unclipped_and_future_frame_never_used(self):
        frames,click=stable_click(raw=(-.4,1.3));frames.append(dict(frames[-1],capture_ns=click['event_ns']+1,raw=[5000,5000]))
        result=sample_clicks(frames,[click],GEOMETRY,small_settings(),(500,0))
        self.assertEqual(result['accepted_count'],1)
        np.testing.assert_allclose(result['samples'][0]['raw'],[-.4,1.3])
        self.assertEqual(result['samples'][0]['last_capture_ns'],click['event_ns'])
        self.assertEqual(result['samples'][0]['frame_count'],10)

    def test_window_end_and_capture_clock_not_server_or_completion_clock(self):
        frames,click=stable_click()
        for row in frames:row.update(inference_completed_ns=10**18,server_ns=10**19,display=[999,999])
        result=sample_clicks(frames,[click],GEOMETRY,small_settings(),(500,100))
        self.assertEqual(result['samples'][0]['frame_count'],8)
        self.assertEqual(result['samples'][0]['last_capture_ns'],click['event_ns']-100_000_000)

    def test_double_click_anchored_first_time_position_without_chaining(self):
        base=dict(event_ns=1_000_000_000,x=.4,y=.4,button='left')
        clicks=[base,dict(base,event_ns=1_400_000_000,x=.42),dict(base,event_ns=1_700_000_000,x=.44),
                dict(base,event_ns=1_800_000_000,x=.9)]
        groups=deduplicate_clicks(clicks,GEOMETRY,small_settings())
        self.assertEqual(len(groups),3);self.assertEqual(groups[0]['merged_presses'],2)
        self.assertIs(groups[0]['click'],base)

    def test_exclusions_close_audit_denominator_and_diagnostics(self):
        frames=[];clicks=[]
        for i in range(6):
            f,c=stable_click(i);frames+=f;clicks.append(c)
        clicks[1]['excluded']=True
        for row in frames:
            if row['frame_id']//10==2:row.update(valid=False,status='no_face',raw=None)
            if row['frame_id']//10==3:row.update(valid=False,status='blink',raw=None)
            if row['frame_id']//10==4:row['head_degrees']=[80,0,0]
            if row['frame_id']//10==5:row['head_degrees']=None
        clicks.append(dict(clicks[0],event_ns=clicks[0]['event_ns']+20_000_000))
        result=sample_clicks(frames,clicks,GEOMETRY,small_settings(),(500,0))
        counts=result['primary_reason_counts']
        self.assertEqual(sum(counts.values()),len(clicks));self.assertEqual(result['accepted_count'],1)
        for reason in ('self_window','no_face','blink','head_pose','missing_head_pose','duplicate_press'):
            self.assertEqual(counts[reason],1)
        self.assertEqual(result['rejected_count'],6)

    def test_unstable_raw_and_numeric_rejection(self):
        frames,click=stable_click()
        for i,row in enumerate(frames):row['raw']=[i%2,0.5]
        sampled=sample_clicks(frames,[click],GEOMETRY,small_settings(),(500,0))
        self.assertIn(sampled['audit'][0]['reason'],('unstable_fixation','position_outlier'))
        frames[0]['raw']=[float('nan'),0]
        self.assertEqual(sample_clicks(frames,[click],GEOMETRY,small_settings(),(500,0))['audit'][0]['reason'],'invalid_numeric')

    def test_dense_short_cluster_fails_actual_time_coverage(self):
        frames,click=stable_click()
        for i,row in enumerate(frames):row['capture_ns']=click['event_ns']-45_000_000+i*5_000_000
        result=sample_clicks(frames,[click],GEOMETRY,small_settings(),(500,0))
        self.assertEqual(result['accepted_count'],0)
        audit=result['audit'][0]
        self.assertEqual(audit['reason'],'insufficient_time_coverage')
        self.assertEqual(audit['window_frame_count'],10)
        self.assertAlmostEqual(audit['window_coverage_ratio'],.09)
        self.assertEqual(audit['max_frame_gap_ms'],5.)
        self.assertEqual(audit['window_last_capture_ns'],click['event_ns'])

    def test_high_valid_ratio_with_large_capture_gap_rejected_separately(self):
        frames,click=stable_click();frames=frames[:6]
        for row,dt in zip(frames,(500,450,400,100,50,0)):
            row['capture_ns']=click['event_ns']-dt*1_000_000
        result=sample_clicks(frames,[click],GEOMETRY,small_settings(),(500,0))
        audit=result['audit'][0]
        self.assertEqual(audit['reason'],'excessive_frame_gap')
        self.assertEqual(audit['window_coverage_ratio'],1.)
        self.assertEqual(audit['max_frame_gap_ms'],300.)
        self.assertEqual(audit['invalid_status_counts'],{})

    def test_three_models_recover_thirty_percent_outliers(self):
        rng=np.random.default_rng(3);raw=rng.uniform(.05,.95,(100,2))
        matrices={'offset':[[1,0],[0,1],[.04,-.06]],'scale_offset':[[1.1,0],[0,.8],[.04,.06]],
                  'affine':[[1.1,.1],[-.12,.8],[.08,.03]]}
        settings=small_settings(ransac_iterations=300,ransac_threshold_cm=.15)
        for model,matrix in matrices.items():
            target=np.column_stack((raw,np.ones(len(raw))))@matrix
            target[:30]=rng.uniform(0,1,(30,2))
            samples=[dict(raw=p.tolist(),target=t.tolist(),event_ns=i*1_000_000_000) for i,(p,t) in enumerate(zip(raw,target))]
            samples=[s for s in samples if all(0<=v<=1 for v in s['target'])]
            with self.subTest(model=model):
                fit=fit_robust(samples,model,GEOMETRY,settings)
                np.testing.assert_allclose(fit['matrix'],matrix,atol=1e-8)
                self.assertGreaterEqual(fit['outlier_count'],29)
                self.assertEqual(fit,fit_robust(samples,model,GEOMETRY,settings))

    def test_degenerate_coverage_and_collinear_fit_fail_explicitly(self):
        samples=[dict(raw=[i/20,i/20],target=[i/20,i/20],event_ns=i*1_000_000_000) for i in range(12)]
        with self.assertRaisesRegex(ValueError,'内点|共线|退化'):fit_robust(samples,'affine',GEOMETRY,small_settings())
        same=[dict(raw=[.5,.5],target=[.5,.5],event_ns=i) for i in range(10)]
        with self.assertRaisesRegex(ValueError,'覆盖'):fit_robust(same,'offset',GEOMETRY,small_settings())
        self.assertEqual(coverage(same)['grid'][1][1],10)

    def test_frozen_rule_boundaries_missing_and_priority(self):
        self.assertEqual(decide_m5(2.,2.,9.)['decision'],'M5 值得做')
        self.assertEqual(decide_m5(2.,None,1.)['decision'],'M5 值得做')
        self.assertIn('点击微调',decide_m5(2.,2.5,3.)['decision'])
        self.assertEqual(decide_m5(2.,2.4999,3.)['decision'],'证据不足')
        self.assertEqual(decide_m5(None,1.,1.)['decision'],'证据不足')
        self.assertEqual(decide_m5(2.,3.,None)['decision'],'证据不足')


if __name__=='__main__':unittest.main()
