"""Synthetic click-offset streams, not measurements or real input hooks."""
from collections import Counter
from dataclasses import replace
import unittest
import numpy as np

from gaze_core.config import AutocalSettings,load_settings
from gaze_core.autocalibration import OnlineOffset
from gaze_core.implicit_calibration import sample_clicks,fit_robust
from test_implicit_calibration import GEOMETRY,small_settings,stable_click


def stream(count=20):
    frames=[];clicks=[]
    for i in range(count):
        target=((.2,.5,.8)[i%3],(.2,.5,.8)[i//3%3])
        f,c=stable_click(i,target=target,raw=(target[0]-.04,target[1]-.08))
        frames+=f;clicks.append(c)
    return frames,clicks


def feed(learner,frames,clicks):
    for _,kind,row in sorted([(r['capture_ns'],0,r) for r in frames]+[(r['event_ns'],1,r) for r in clicks],key=lambda x:x[:2]):
        if kind==0:learner.frame(row)
        else:learner.click(row)
    learner.finish(clicks[-1]['event_ns'])


class OnlineOffsetTests(unittest.TestCase):
    def test_default_and_invalid_configuration(self):
        options=load_settings(use_local=False).autocal
        self.assertFalse(options.enabled);self.assertEqual(list(options.window_ms),[300,0])
        self.assertEqual(options.max_clicks,120);self.assertEqual(options.max_fps,'inference')
        self.assertEqual(replace(AutocalSettings(),window_ms=options.window_ms),options)
        for values in (dict(min_clicks=True),dict(max_clicks=2),dict(min_occupied_cells=2),dict(window_ms=[0,200]),dict(max_fps='mps'),dict(frame_history_s=.01)):
            with self.subTest(values=values),self.assertRaises((ValueError,TypeError)):AutocalSettings(**values)
        inherited=AutocalSettings(max_fps='inference')
        self.assertEqual(inherited.inference_limit(load_settings(use_local=False).inference),15)
        self.assertEqual(inherited.inference_limit(replace(load_settings(use_local=False).inference,max_fps=12)),12)
        self.assertIsNone(inherited.inference_limit(replace(load_settings(use_local=False).inference,max_fps=None)))

    def test_online_and_batch_share_samples_rejections_and_robust_fit_with_numpy(self):
        frames,clicks=stream();clicks.insert(1,dict(clicks[0],event_ns=clicks[0]['event_ns']+20_000_000))
        clicks[5]=dict(clicks[5],excluded=True,exclusion_reason='own_panel')
        sampling=small_settings(min_occupied_cells=3)
        learner=OnlineOffset(GEOMETRY,sampling,AutocalSettings(window_ms=(200,0)));feed(learner,frames,clicks)
        batch=sample_clicks(frames,clicks,GEOMETRY,sampling,(200,0))
        self.assertEqual(learner.accepted,batch['accepted_count'])
        self.assertEqual(dict(learner.reasons),batch['primary_reason_counts'])
        self.assertEqual(list(learner.samples),batch['samples'])
        expected=fit_robust(batch['samples'],'offset',GEOMETRY,sampling)
        np.testing.assert_allclose(learner.state()['matrix'],expected['matrix'],atol=1e-12)

    def test_late_inference_waits_capture_watermark_not_response_time(self):
        frames,clicks=stream(1);learner=OnlineOffset(GEOMETRY,small_settings())
        for row in frames[:-1]:learner.frame(row)
        learner.click(clicks[0]);self.assertEqual(len(learner.pending),1)
        learner.frame(dict(frames[-1],inference_completed_ns=10**18))
        self.assertEqual(len(learner.pending),0);self.assertEqual(learner.accepted,1)
        self.assertEqual(learner.samples[0]['last_capture_ns'],clicks[0]['event_ns'])
        other=OnlineOffset(GEOMETRY,small_settings());other.click(clicks[0])
        other.advance(clicks[0]['event_ns']+3_000_000_000)
        self.assertEqual(other.reasons['inference_watermark_timeout'],1)

    def test_rolling_capacity_and_minimum_and_coverage_gates(self):
        frames,clicks=stream(60);learner=OnlineOffset(GEOMETRY,small_settings(min_occupied_cells=3))
        feed(learner,frames[:90],clicks[:9]);self.assertIsNone(learner.fit)
        # Restart for the full stream; the first feed consumed later camera stamps.
        learner=OnlineOffset(GEOMETRY,small_settings(min_occupied_cells=3),AutocalSettings(max_clicks=40));feed(learner,frames,clicks)
        self.assertEqual(len(learner.samples),40);self.assertEqual(learner.samples[0]['event_ns'],clicks[20]['event_ns'])
        self.assertLessEqual(len(learner.frames),learner.options.queue_size)
        same_frames=[];same_clicks=[]
        for i in range(12):
            f,c=stable_click(i);same_frames+=f;same_clicks.append(c)
        insufficient=OnlineOffset(GEOMETRY,small_settings());feed(insufficient,same_frames,same_clicks)
        self.assertIsNone(insufficient.fit);self.assertIn('覆盖',insufficient.fit_error)

    def test_provisional_default_retains_120_and_rolls_without_relaxing_sampling(self):
        frames,clicks=stream(125);sampling=small_settings(min_occupied_cells=3)
        learner=OnlineOffset(GEOMETRY,sampling);feed(learner,frames,clicks)
        batch=sample_clicks(frames,clicks,GEOMETRY,sampling,(300,0))
        self.assertEqual(learner.sampling.min_frames,sampling.min_frames)
        self.assertEqual(learner.accepted,batch['accepted_count'])
        self.assertEqual(len(learner.samples),120)
        self.assertEqual(list(learner.samples),batch['samples'][-120:])
        expected=fit_robust(batch['samples'][-120:],'offset',GEOMETRY,sampling)
        np.testing.assert_allclose(learner.state()['matrix'],expected['matrix'],atol=1e-12)

    def test_duplicate_anchor_expires_without_chaining_through_duplicate(self):
        learner=OnlineOffset(GEOMETRY,small_settings())
        base=dict(event_ns=1_000_000_000,x=.4,y=.4,excluded=False)
        for t in (0,.4,.7):learner.click(dict(base,event_ns=base['event_ns']+round(t*1e9)))
        learner.finish(2_000_000_000)
        self.assertEqual(learner.reasons['duplicate_press'],1)
        self.assertEqual(learner.reasons['insufficient_frames'],2)

    def test_half_rate_and_original_public_check_are_separate(self):
        from tools.replay_autocal import compare
        from gaze_core.implicit_experiment import read_experiments
        from test_implicit_experiment import recording
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.jsonl';recording(path)
            trial=read_experiments(path)[0][0];before=path.read_bytes()
            result=compare(trial,small_settings(min_occupied_cells=3))
            for name in ('a','b','c'):
                for row in result['scenarios'][name].values():
                    self.assertEqual(row['batch_accepted'],row['accepted'])
            self.assertEqual(result['scenarios']['b']['all']['training_frame_count'],len(trial.frames)//2)
            self.assertEqual(result['public_check']['frame_ids'],[r['frame_id'] for r in trial.check_frames])
            self.assertEqual(result['scenarios']['a']['all']['mean_difference_vs_M4_original_all_cm'],0.)
            extra=result['scenarios']['c']['recent_120']
            self.assertEqual(extra['capacity'],120);self.assertEqual(extra['window_ms'],[300,0])
            self.assertEqual(extra['training_frame_count'],result['scenarios']['c']['all']['training_frame_count'])
            self.assertEqual(extra['summary']['groups']['A'],result['baselines']['groups']['A'])
            self.assertEqual(result['decision']['revision'],'post_hoc_user_correction_one_sided')
            self.assertEqual(result['original_decision']['revision'],'original_predeclared_absolute')
            self.assertEqual(path.read_bytes(),before)

    def test_default_decision_requires_both_counts_and_never_rounds_over_threshold(self):
        from tools.replay_autocal import choose_defaults
        from copy import deepcopy
        base={key:{label:dict(C=dict(all=dict(mean_cm=2.,complete=True))) for label in ('all','recent_40')} for key in ('a','c')}
        self.assertTrue(choose_defaults(base)['passed'])
        base['c']['recent_40']['C']['all']['mean_cm']=2.151
        self.assertFalse(choose_defaults(base)['passed'])
        self.assertIsNone(choose_defaults(base)['max_fps'])
        base['c']['recent_40']['C']['all']['mean_cm']=2.15
        self.assertTrue(choose_defaults(base)['passed'])
        base['c']['all']['C']['all']['complete']=False
        self.assertFalse(choose_defaults(base)['passed'])

    def test_posthoc_one_sided_rule_accepts_improvement_preserves_original_failure(self):
        from tools.replay_autocal import choose_defaults
        rows={key:{label:dict(C=dict(all=dict(mean_cm=np.float64(2.),complete=True))) for label in ('all','recent_40')} for key in ('a','c')}
        rows['c']['all']['C']['all']['mean_cm']=np.float64(2.006)
        rows['c']['recent_40']['C']['all']['mean_cm']=np.float64(1.845)
        revised=choose_defaults(rows);original=choose_defaults(rows,absolute=True)
        self.assertTrue(revised['passed']);self.assertEqual(revised['window_ms'],[300,0]);self.assertEqual(revised['max_fps'],'inference')
        self.assertLess(revised['differences_cm']['recent_40'],0)
        self.assertFalse(original['passed']);self.assertEqual(original['window_ms'],[200,0]);self.assertIsNone(original['max_fps'])
        rows['c']['recent_40']['C']=None
        self.assertFalse(choose_defaults(rows)['passed'])

    def test_unavailable_replay_is_reported_without_fabricated_C_or_fps(self):
        from tools.replay_autocal import compare,save_report
        from gaze_core.implicit_experiment import read_experiments
        from test_implicit_experiment import recording
        from unittest.mock import patch
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.jsonl';recording(path)
            trial=read_experiments(path)[0][0];trial.frames=[];trial.clicks=[]
            result=compare(trial,small_settings(min_occupied_cells=3))
            with patch('tools.replay_autocal.ROOT',Path(folder)):
                report=save_report(result,Path(folder)/'data/empty')
            self.assertIn('不可用',(report/'report.md').read_text())
            self.assertIn('事后修正',(report/'report.md').read_text())
            self.assertIn('原预登记绝对差规则',(report/'report.md').read_text())
            self.assertFalse(result['decision']['passed'])


if __name__=='__main__':unittest.main()
