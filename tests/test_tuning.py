"""Synthetic numeric signals only; never publish these as tuning measurements."""
from contextlib import redirect_stdout,redirect_stderr
from dataclasses import asdict,replace
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from gaze_core.config import load_settings,ScreenGeometry,FilterSettings,FixationSettings
from gaze_core.tuning import (JumpTask,TuningSettings,read_jump_tasks,simulate,measure_task,tune,
                              crossing_time,jitter,match_saccades,pareto_front,choose_knee)
from gaze_core.recorder import SessionRecorder,session_metadata
from tools.tune_filter import write_report,main


def synthetic_task(identifier='s0'):
    g=ScreenGeometry(width_cm=30,height_cm=20,top_cm=0,focal_scale=1,measured=True)
    targets=[dict(time_s=float(i),point=p,index=i) for i,p in enumerate([[.2,.2],[.8,.2],[.2,.8]])]
    matrix=[[1,0],[0,2],[0,0]]
    frames=[]
    for i in range(150):
        t=i/50
        active=max(0,min(2,int(max(0,t-.1))))
        point=np.array(targets[active]['point'])+(.001 if i%2 else -.001)
        raw=[np.float64(point[0]),np.float64(point[1]/2)]
        frames.append(dict(time_s=t,matrix=matrix,data=dict(valid=True,status='tracking',raw=raw,
                      origin_m=[0,.1,.5],browser_capture_ms=t*1000,inference_completed_ns=100_000_000+i*999_999)))
    return JumpTask(file=identifier+'.jsonl',digest=identifier,task_id=identifier,geometry=g,targets=targets,
                    frames=frames,end_s=3.,completed=True,group='B',condition=('synthetic',))


def small_config(**kwargs):
    return TuningSettings(min_cutoff=(1.,),beta=(0.,),d_cutoff=(1.,),angular_threshold_deg_s=(30.,),
                          expected_points=3,**kwargs)


class TuningTests(unittest.TestCase):
    def test_replay_applies_matrix_and_uses_capture_time(self):
        task=synthetic_task();samples,segments,events=simulate(task,FilterSettings(),FixationSettings(angular_threshold_deg_s=30))
        np.testing.assert_allclose(samples[0]['unfiltered'],[.199,.199])
        self.assertTrue(events);self.assertTrue(segments)
        self.assertEqual(samples[1]['time_s'],.02)
        self.assertLess(max(r['duration_ms'] for r in segments),3000)
    def test_latency_same_90_threshold_and_negative_difference_not_clamped(self):
        g=synthetic_task().geometry
        rows=[dict(time_s=1.,unfiltered=[.2,.2],position=[.2,.2]),
              dict(time_s=1.1,unfiltered=[.8,.2],position=[.4,.2]),
              dict(time_s=1.2,unfiltered=[.8,.2],position=[.75,.2])]
        raw=crossing_time(rows,'unfiltered',[.2,.2],[.8,.2],g,.9,0)
        filtered=crossing_time(rows,'position',[.2,.2],[.8,.2],g,.9,0)
        self.assertAlmostEqual((filtered-raw)*1000,100)
        self.assertAlmostEqual((raw-filtered)*1000,-100)
        self.assertIsNone(crossing_time(rows,'position',[.2,.2],[.8,.2],g,.9,100))
    def test_jitter_is_position_sd_not_bias_and_numpy_origin(self):
        rows=[dict(unfiltered=[.8,.9],origin_m=np.array([0.,.1,.5])) for _ in range(5)]
        result=jitter(rows,'unfiltered',synthetic_task().geometry)
        self.assertAlmostEqual(result['cm'],0,places=12);self.assertAlmostEqual(result['deg'],0,places=12)
        rows[0]['origin_m']=None
        self.assertEqual(jitter(rows,'unfiltered',synthetic_task().geometry)['angle_frames'],4)
    def test_event_matching_denominators(self):
        r=match_saccades([1,2,3],[.1,1.1,1.2,2.1,4],.5)
        self.assertEqual((r['matched'],r['missed'],r['false_detections'],r['detected_saccades']),(2,1,2,4))
        self.assertAlmostEqual(r['missed_rate'],1/3);self.assertEqual(r['false_detection_share'],.5)
    def test_common_windows_preserve_noisy_saccade_frames_and_coverage(self):
        task=synthetic_task();config=small_config()
        detector=FixationSettings(angular_threshold_deg_s=30)
        samples,segments,events=simulate(task,FilterSettings(),detector)
        a=measure_task(task,samples,segments,events,config,detector)
        for row in samples:row['state']='saccade'
        b=measure_task(task,samples,[],events,config,detector)
        self.assertEqual(a['jitter_cm'],b['jitter_cm']);self.assertEqual(b['fixation_coverage'],0)
        self.assertEqual(a['expected_transitions'],2);self.assertEqual(a['paired_transitions'],2)
        for row in samples:row['position']=task.targets[max(0,min(1,int(row['time_s'])-1))]['point']
        missing=measure_task(task,samples,[],events,config,detector)
        self.assertEqual(missing['filtered_unreached'],2);self.assertIsNone(missing['extra_delay_ms'])
    def test_pareto_knee_and_no_unqualified_recommendation(self):
        candidates=[dict(extra_delay_ms=x,jitter_cm=y) for x,y in [(0,10),(2,4),(10,0),(5,8)]]
        front=pareto_front(candidates);self.assertEqual(front,[0,1,2]);self.assertEqual(choose_knee(candidates,front),1)
        result=tune([synthetic_task()],small_config())
        self.assertIsNone(result['recommended']);self.assertIn('insufficient_tasks',result['candidates'][0]['rejection_reasons'])
        result=tune([synthetic_task(str(i)) for i in range(3)],small_config())
        self.assertEqual(result['recommended'],0)
        repeated=tune([synthetic_task()]*3,small_config())
        self.assertIsNone(repeated['recommended']);self.assertEqual(len(repeated['skipped']),2)
    def test_mixed_conditions_and_missing_angle_do_not_produce_defaults(self):
        tasks=[synthetic_task(str(i)) for i in range(3)];tasks[2].condition=('other-device',)
        self.assertEqual(tune(tasks,small_config())['candidates'],[])
        tasks=[synthetic_task(str(i)) for i in range(3)]
        for task in tasks:
            for row in task.frames:row['data']['origin_m']=None
        result=tune(tasks,small_config())
        self.assertIsNone(result['recommended']);self.assertIn('angle_coverage',result['candidates'][0]['rejection_reasons'])
    def test_bad_search_settings(self):
        for change in [dict(beta=(-1,)),dict(min_cutoff=()),dict(min_tasks=0),dict(min_angle_coverage=2)]:
            with self.assertRaises(ValueError):replace(small_config(),**change)

    def test_unscorable_gap_is_not_counted_as_false_detection(self):
        task=synthetic_task();detector=FixationSettings(angular_threshold_deg_s=30)
        samples,segments,events=simulate(task,FilterSettings(),detector)
        # No valid data for the first commanded jump; its detections cannot be labelled.
        for row in samples:
            if 1<=row['time_s']<2:row['position']=row['unfiltered']=None
        measured=measure_task(task,samples,segments,[1.1,2.1],small_config(),detector)
        self.assertEqual(measured['invalid_transitions'],1)
        self.assertEqual(measured['expected_switches'],1)
        self.assertEqual(measured['false_detections'],0)

    def write_recording(self,folder,*,measured=True,coordinate_system='full_screen_normalized'):
        settings=load_settings(use_local=False);task=synthetic_task()
        engine=SimpleNamespace(device='cpu',device_name='test',settings=settings)
        metadata=session_metadata(source='web',engine=engine,geometry=task.geometry,camera_size=(640,480),
                                  grayscale=False,screen=dict(fullscreen=True,width_px=1000,height_px=800),measured=measured,page_clock_id='test')
        r=SessionRecorder(metadata,directory=folder)
        cal=dict(action='applied',matrix=[[1,0],[0,2],[0,0]],calibration_id='c',group='B',browser_time_ms=0)
        if coordinate_system:cal['coordinate_system']=coordinate_system
        r.emit('calibration',**cal)
        for index,target in enumerate(task.targets):
            r.emit('target',action='appear' if index==0 else 'switch',task_type='jump',task_id='jump',index=index,
                   target=target['point'],stage_target=None,browser_time_ms=target['time_s']*1000)
            for frame in task.frames[index*50:(index+1)*50]:
                data={k:frame['data'][k] for k in ('valid','status','raw','origin_m')}
                r.record_frame(data,browser_capture_ms=frame['data']['browser_capture_ms'],inference_completed_ns=1_000_000_000)
        r.emit('target',action='end',task_type='jump',task_id='jump',index=2,target=task.targets[-1]['point'],
               stage_target=None,browser_time_ms=3000,reason='completed')
        r.close('test',3000)
        return r.path

    def test_real_jsonl_replay_clock_calibration_and_legacy_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.write_recording(folder)
            tasks=read_jump_tasks(path);self.assertEqual(len(tasks),1)
            self.assertFalse(tasks[0].reasons);self.assertEqual(tasks[0].frames[0]['matrix'],[[1,0],[0,2],[0,0]])
            self.assertEqual(tasks[0].frames[1]['time_s'],.02)
            bad=read_jump_tasks(self.write_recording(folder,measured=False,coordinate_system=None))
            reasons=tune(bad,small_config())['skipped'][0]['reasons']
            self.assertIn('unmeasured_screen',reasons);self.assertIn('unknown_calibration_coordinates',reasons)

    def test_report_plot_and_machine_readable_results_are_only_temporary_synthetic(self):
        config=small_config();result=tune([synthetic_task(str(i)) for i in range(3)],config)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic-report.md'
            write_report(result,config,FixationSettings(),path,['synthetic']*3)
            text=path.read_text();self.assertIn('推荐候选',text);self.assertIn('FP/(TP+FP)',text)
            self.assertEqual(path.with_name('synthetic-report-pareto.png').read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
            values=json.loads(path.with_suffix('.json').read_text());self.assertEqual(values['recommended'],0)
    def test_pending_cli_has_no_fake_results_or_chart(self):
        with tempfile.TemporaryDirectory() as folder,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            path=Path(folder)/'pending.md'
            self.assertEqual(main(['--pending','--output',str(path)]),0)
            text=path.read_text();self.assertIn('待实测',text);self.assertIn('没有生成测量数字',text)
            self.assertFalse(path.with_name('pending-pareto.png').exists())
            values=json.loads(path.with_suffix('.json').read_text());self.assertEqual(values['candidates'],[]);self.assertIsNone(values['recommended'])

if __name__=='__main__':unittest.main()
