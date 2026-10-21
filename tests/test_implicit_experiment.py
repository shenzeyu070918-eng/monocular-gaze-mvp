"""End-to-end experiments use temporary synthetic numeric JSONL only."""
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
from dataclasses import asdict,replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from gaze_core.config import ROOT
from gaze_core.evaluation import evaluate
from gaze_core.implicit_experiment import (read_experiments,select_training,analyze_recordings,load_manifest,
                                           summaries_match,exploratory_analysis)
from tools.implicit_calib_experiment import main,write_report
from test_implicit_calibration import GEOMETRY,small_settings,stable_click


def recording(path,*,complete=True,exact=True,invalid_collect_frame=False,check_delay_s=.1):
    rows=[];sequence=0;session='synthetic-session';matrix=[[1,0],[0,1],[.03,.07]]
    def add(kind,**fields):
        nonlocal sequence
        row=dict(type=kind,schema_version=1,session_id=session,seq=sequence,server_ns=10**15+sequence,**fields)
        rows.append(row);sequence+=1;return row
    add('session',source='python',screen=dict(width_cm=30.,height_cm=20.,measured=True,top_cm=0.,horizontal_cm=0.))
    base=dict(experiment_id='trial-1',baseline_matrix=matrix,baseline_calibration_id='calib-new',
              baseline_created_at='2026-10-21T00:00:00+00:00',baseline_age_s=30.,conditions_fingerprint='synthetic-condition')
    add('click_session',action='started',event_ns=100_000_000,**base)
    add('click_session',action='baseline',event_ns=200_000_000,**base)
    start=1_000_000_000
    add('click_session',action='collecting',event_ns=start,collection_start_ns=start,max_fps=None,**base)
    frames=[]
    for i in range(24):
        target=[(.2,.5,.8)[i%3],(.2,.5,.8)[(i//3)%3]]
        f,c=stable_click(i,target=target,raw=(target[0]-.04,target[1]-.08))
        for frame in f:
            raw=[float(v) for v in frame.pop('raw')];head=[float(v) for v in frame.pop('head_degrees')]
            frame.update(raw=raw,head_degrees=head)
            if invalid_collect_frame and i==0 and frame['frame_id']==0:frame.update(valid=False,status='no_face',raw=None)
            frames.append(add('frame',**frame))
        add('click',**c)
    end=frames[-1]['capture_ns']+500_000_000
    times=[r['capture_ns'] for r in frames];fps=(len(times)-1)*1e9/(times[-1]-times[0])
    add('click_session',action='collection_ended',event_ns=end,collection_start_ns=start,collection_end_ns=end,
        inference_frame_count=len(frames),actual_inference_fps=fps,max_fps=None,**base)
    check_start=end+int(check_delay_s*1e9)
    add('click_session',action='check_started',event_ns=check_start,check_task_id='check-1',**base)
    targets=[dict(target_id='check-center',region='center',target=[.25,.34]),
             dict(target_id='check-edge',region='edge',target=[.95,.95])]
    selected=[];check=[]
    for ti,target in enumerate(targets):
        add('target',action='appear' if ti==0 else 'switch',task_type='evaluation',task_id='check-1',
            target=target['target'],target_id=target['target_id'],region=target['region'],event_ns=check_start+int((ti+1)*1e9))
        for j in range(5):
            frame=add('frame',frame_id=1000+ti*10+j,capture_ns=check_start+int((ti+1.2+j*.05)*1e9),raw=[target['target'][0]-.04,target['target'][1]-.08],
                      valid=True,status='tracking',head_degrees=[0,0,0],origin_m=[0,0,.5],display=[100,100])
            selected.append(dict(frame_id=frame['frame_id'],target_id=target['target_id']))
            check.append(dict(frame,target_id=target['target_id']))
    summary=evaluate(check,targets,GEOMETRY,matrix)
    fields=dict(task_id='check-1',run_id='run-1',calibration_id='calib-new',completed=True,reason='completed',
                conditions_fingerprint='synthetic-condition',summary=summary,event_ns=check_start+4_000_000_000)
    if exact:fields.update(sample_selection=selected,sampling=dict(settle_ms=650.,dwell_ms=2200.,max_dwell_ms=10000.))
    add('evaluation_summary',**fields)
    if complete:add('click_session',action='completed',event_ns=check_start+5_000_000_000,check_task_id='check-1',**base)
    else:add('click_session',action='aborted',event_ns=check_start+5_000_000_000,reason='cancelled',**base)
    add('end',written_lines=len(rows)+1,failed_records=0,reason='normal')
    path.write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in rows),encoding='utf-8')
    return rows


class ImplicitExperimentTests(unittest.TestCase):
    def test_tolerant_comparison_keeps_json_structure_and_non_numeric_values_exact(self):
        original=dict(value=np.float64(1.),near_zero=0.,points=[dict(region='edge',complete=True,error=None)])
        close=deepcopy(original);close.update(value=1.+5e-10,near_zero=5e-13)
        self.assertTrue(summaries_match(original,close))
        for altered in (dict(close,value=1.+2e-9),dict(close,near_zero=2e-12),dict(close,extra=1),
                        dict(close,points=[]),dict(close,points=tuple(close['points'])),
                        dict(close,points=[dict(region='center',complete=True,error=None)]),
                        dict(close,points=[dict(region='edge',complete=1,error=None)]),
                        dict(close,points=[dict(region='edge',complete=True,error=0.)])):
            with self.subTest(altered=altered):self.assertFalse(summaries_match(original,altered))
        self.assertFalse(summaries_match(dict(value=float('nan')),dict(value=float('nan'))))
        self.assertFalse(summaries_match(dict(value=float('inf')),dict(value=float('inf'))))

    def test_panel_roundoff_does_not_block_m5_but_real_or_structural_difference_does(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'roundoff.jsonl';source=recording(path)
            for case in ('roundoff','numeric_mismatch','extra_key'):
                rows=deepcopy(source)
                panel=next(r for r in rows if r['type']=='evaluation_summary')['summary']['groups']
                panel['A']['all']['mean_cm']+=1e-14
                panel['B']['all']['mean_cm']+=1e-15
                if case=='numeric_mismatch':panel['B']['all']['mean_cm']+=1e-5
                elif case=='extra_key':panel['B']['all']['unexpected']=None
                path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                trial=analyze_recordings([path],small_settings())['experiments'][0]
                with self.subTest(case=case):
                    self.assertEqual(trial['panel_recomputed_exactly'],case=='roundoff')
                    self.assertEqual(trial['decision']['decision'],'M5 值得做' if case=='roundoff' else '证据不足')
                    self.assertEqual(trial['panel_comparison']['relative_tolerance'],1e-9)
                    self.assertEqual(trial['panel_comparison']['absolute_tolerance'],1e-12)

    def test_exploratory_median_offset_preserves_B_linear_part_and_uses_same_check(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path)
            trials,_=read_experiments(path);trial=trials[0]
            matrix=np.array([[1.2,.1],[.2,.7],[.1,-.2]])
            trial.baseline['baseline_matrix']=matrix.tolist()
            trial.collection['collection_start_ns']=0;trial.check_start_ns=1_200_000_000_000
            raw=np.array([[.2,.3],[.4,.6],[.5,.6]])
            residual_cm=np.array([[-2.,3.],[1.,4.],[3.,5.]])
            targets=np.column_stack((raw,np.ones(3)))@matrix+residual_cm/[30.,20.]
            samples=[dict(raw=raw[i].tolist(),target=targets[i].tolist(),event_ns=t)
                     for i,t in enumerate((100_000_000_000,650_000_000_000,1_150_000_000_000))]
            trained=dict(selection=dict(window_index=0),samples_by_window=[dict(samples=samples)])
            before=deepcopy((trial,trained));result=exploratory_analysis(trial,trained)
            self.assertFalse(result['participates_in_m5'])
            residual=result['B_click_residuals']
            self.assertAlmostEqual(residual['median_horizontal_cm'],1.)
            self.assertAlmostEqual(residual['median_vertical_cm'],4.)
            self.assertAlmostEqual(residual['median_absolute_horizontal_cm'],2.)
            self.assertAlmostEqual(residual['median_absolute_vertical_cm'],4.)
            self.assertAlmostEqual(residual['median_absolute_error_cm'],np.sqrt(17.))
            rows=result['results'];self.assertEqual([r['sample_count'] for r in rows],[3,2,1,3])
            expected=matrix.copy();expected[2]+=[1/30,4/20]
            np.testing.assert_allclose(rows[0]['matrix'],expected)
            self.assertEqual(rows[0]['summary'],evaluate(trial.check_frames,trial.targets,trial.geometry,expected)['groups']['B'])
            delta=np.median(targets-raw,axis=0)
            np.testing.assert_allclose(rows[-1]['matrix'],[[1,0],[0,1],delta.tolist()])
            np.testing.assert_allclose(trial.baseline['baseline_matrix'],before[0].baseline['baseline_matrix'])
            self.assertEqual(trained,before[1])
            trial.check_start_ns+=1_000_000_000_000
            empty=exploratory_analysis(trial,dict(trained,selection=None))
            self.assertFalse(empty['results'][1]['ok']);self.assertIsNone(empty['results'][1]['summary'])
            self.assertEqual(empty['window_source'],'first_configured_exploratory_only')

    def test_exploration_cannot_change_preregistered_selection_scores_or_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path)
            before=analyze_recordings([path],small_settings())['experiments'][0]
            with patch('gaze_core.implicit_experiment.exploratory_analysis',return_value=dict(decision='九点 + 点击微调')):
                altered=analyze_recordings([path],small_settings())['experiments'][0]
            for key in ('decision','selection','C_all','C_recent','count_results','recent_results'):
                self.assertEqual(before[key],altered[key])
            unavailable=analyze_recordings([path],replace(small_settings(),min_training_clicks=100))['experiments'][0]
            self.assertIsNone(unavailable['selection'])
            self.assertEqual(unavailable['decision']['decision'],'证据不足')
            self.assertTrue(unavailable['exploratory']['results'][0]['ok'])

    def test_exact_shared_frames_recompute_panel_and_readonly_single_user(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.jsonl';recording(path,invalid_collect_frame=True)
            profile=Path(folder)/'profile.json';profile.write_text('{"matrix":"DO NOT READ OR WRITE"}')
            original=path.read_bytes();profile_original=profile.read_bytes()
            result=analyze_recordings([path],small_settings())
            self.assertEqual(result['sample_size'],dict(subjects=1,machines=1,experiments=1))
            trial=result['experiments'][0]
            self.assertTrue(trial['panel_recomputed_exactly']);self.assertEqual(trial['check_frame_count'],10)
            self.assertEqual(len(trial['check_frame_ids']),10)
            self.assertTrue(trial['C_all']['ok']);self.assertAlmostEqual(trial['C_all']['summary']['all']['mean_cm'],0,places=5)
            self.assertEqual(trial['decision']['decision'],'M5 值得做')
            self.assertEqual(trial['collection']['frame_count'],240)
            self.assertEqual(trial['collection']['valid_frame_count'],239)
            self.assertEqual(trial['collection']['fps_difference'],0)
            self.assertEqual(path.read_bytes(),original);self.assertEqual(profile.read_bytes(),profile_original)

    def test_public_frame_perturbation_never_changes_training_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path)
            trials,_=read_experiments(path);trial=trials[0]
            chosen=select_training(trial,small_settings())
            altered=deepcopy(trial)
            for frame in altered.check_frames:frame['raw']=[9000,-9000]
            for target in altered.targets:target['target']=[-900,900]
            altered.baseline['baseline_matrix']=[[90,90],[90,90],[90,90]]
            after=select_training(altered,small_settings())
            self.assertEqual(chosen,after)

    def test_recent_minutes_anchor_final_check_not_earlier_collection_end(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path,check_delay_s=241.)
            trials,skipped=read_experiments(path);self.assertFalse(skipped)
            trial=trials[0];selected=select_training(trial,small_settings())
            self.assertIsNotNone(selected['selection'])
            self.assertIsNone(selected['selection']['recent_minutes'])
            self.assertTrue(all(row['accepted_clicks']==0 for row in selected['recent_scan']))
            result=analyze_recordings([path],small_settings())['experiments'][0]
            self.assertEqual(result['collection']['wait_before_check_seconds'],241.)
            self.assertTrue(all(row['count']==0 for row in result['recent_results']))
            self.assertIsNone(result['C_recent'])
            altered=deepcopy(trial)
            for frame in altered.check_frames:frame['raw']=[9000,-9000]
            self.assertEqual(select_training(trial,small_settings()),select_training(altered,small_settings()))

    def test_check_start_is_bound_to_completed_task_when_previous_check_cancelled(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';rows=recording(path,check_delay_s=241.)
            final=next(row for row in rows if row.get('action')=='check_started')
            previous=dict(final,check_task_id='cancelled-earlier-check',event_ns=final['event_ns']-240_000_000_000)
            rows.insert(rows.index(final),previous)
            for seq,row in enumerate(rows):row['seq']=seq
            rows[-1]['written_lines']=len(rows)
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            trials,skipped=read_experiments(path);self.assertFalse(skipped)
            self.assertEqual(trials[0].check_start_ns,final['event_ns'])

    def test_subsets_are_reproducible_and_models_share_indices(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path)
            first=analyze_recordings([path],small_settings())['experiments'][0]
            second=analyze_recordings([path],small_settings())['experiments'][0]
            self.assertEqual(first,second)
            count=next(row for row in first['count_results'] if row['count']==10)
            self.assertEqual(len(count['repetitions']),9)
            for repeat in range(3):
                indices=[row['selected_indices'] for row in count['repetitions'] if row['repeat']==repeat]
                self.assertTrue(all(row==indices[0] for row in indices))
                self.assertEqual(len(set(indices[0])),10)

    def test_cancelled_legacy_missing_exact_and_duplicate_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path,complete=False)
            result=analyze_recordings([path],small_settings())
            self.assertFalse(result['experiments']);self.assertIn('click_session_not_completed',result['skipped'][0]['reasons'])
            recording(path,exact=False)
            self.assertIn('missing_exact_check_sample_selection',analyze_recordings([path],small_settings())['skipped'][0]['reasons'])
            recording(path)
            result=analyze_recordings([path,path],small_settings())
            self.assertEqual(len(result['experiments']),1);self.assertEqual(result['skipped'][0]['reasons'],['duplicate_input'])

    def test_invalid_selection_condition_and_baseline_time_do_not_silently_fit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';source=recording(path)
            for change in ('frame','condition','time'):
                rows=deepcopy(source)
                if change=='frame':next(r for r in rows if r['type']=='evaluation_summary')['sample_selection'][0]['frame_id']=-500
                elif change=='condition':next(r for r in rows if r.get('action')=='completed')['conditions_fingerprint']='different'
                else:next(r for r in rows if r.get('action')=='baseline').pop('baseline_created_at')
                path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                with self.subTest(change=change):self.assertFalse(analyze_recordings([path],small_settings())['experiments'])

    def test_manifest_and_reports_include_audits_and_graphs(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'recording.jsonl';recording(path)
            manifest=Path(folder)/'participants.json'
            manifest.write_text(json.dumps(dict(recordings=[dict(file=path.name,subject='p1',machine='mac1')])))
            result=analyze_recordings([path],small_settings(),manifest=load_manifest(manifest))
            output=Path(folder)/'implicit_calibration.md';artifacts=write_report(result,output)
            text=output.read_text();self.assertIn('p1 / mac1',text);self.assertIn('互斥筛选',text)
            self.assertTrue(artifacts['json'].is_file());self.assertTrue(artifacts['csv'].is_file())
            self.assertEqual(len(artifacts['images']),1)
            self.assertGreater(artifacts['images'][0].stat().st_size,100)
            data=json.loads(artifacts['json'].read_text());self.assertEqual(data['sample_size']['subjects'],1)
            self.assertIn('固定种子',text);self.assertIn('采集开始时',text)
            self.assertIn('### 非预登记探索分析（不参与 M5 判定）',text)
            self.assertIn('本节为非预登记探索分析，不参与 M5 判定',text)
            self.assertIn('仅点击中位数偏移（不叠加 B）',text)
            self.assertIn('最近 10 分钟',text);self.assertIn('前 10 分钟',text)
            self.assertFalse(data['experiments'][0]['exploratory']['participates_in_m5'])
            self.assertIn('exploratory_not_for_m5',artifacts['csv'].read_text())

    def test_cli_works_and_never_touches_input_or_profiles(self):
        (ROOT/'data').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder:
            path=Path(folder)/'synthetic.jsonl';recording(path)
            output=Path(folder)/'implicit_calibration.md';config=Path(folder)/'experiment.yaml'
            import yaml
            config.write_text(yaml.safe_dump(asdict(small_settings())))
            before=path.read_bytes()
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                status=main([str(path),'--config',str(config),'--output',str(output),'--no-plots'])
            self.assertEqual(status,0);self.assertEqual(before,path.read_bytes());self.assertTrue(output.is_file())
            self.assertIn('M5 值得做',output.read_text())
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--output',str(Path(folder).parents[1]/'leaky.md')]),1)
                self.assertEqual(main(['--output',str(ROOT/'data/profiles/forbidden.md')]),1)

    def test_report_cannot_overwrite_manifest_or_profile(self):
        (ROOT/'data').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder:
            manifest=Path(folder)/'implicit_calibration.json'
            original=json.dumps(dict(recordings=[]));manifest.write_text(original)
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--manifest',str(manifest),'--output',str(manifest.with_suffix('.md'))]),1)
            self.assertEqual(manifest.read_text(),original)
            with self.assertRaisesRegex(ValueError,'档案'):
                write_report(analyze_recordings([],small_settings()),ROOT/'data/profiles/forbidden.md')

    def test_no_data_report_has_no_fabricated_score(self):
        result=analyze_recordings([],small_settings())
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'implicit_calibration.md';write_report(result,output)
            self.assertIn('没有生成精度成绩',output.read_text());self.assertFalse(result['experiments'])


if __name__=='__main__':unittest.main()
