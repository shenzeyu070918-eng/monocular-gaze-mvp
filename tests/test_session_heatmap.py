"""Offline acceptance uses 14 synthetic Python check targets, never a camera."""
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
from dataclasses import replace
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from gaze_core.config import ROOT,load_settings,RecordingSettings
from gaze_core.heatmap_export import export_heatmap
from gaze_core.session_heatmap import CalibrationTimeline,session_heatmap
from gaze_core.filters import zero_phase_smooth
from gaze_core.recorder import validate_payload,SessionRecorder
from tools.session_heatmap import main
from test_heatmap import GEOMETRY,OPTIONS,IDENTITY
from test_recorder import metadata,frame


def recording(path,*,versioned=True,uncalibrated=False,measured=True):
    rows=[]
    def add(kind,t,**fields):
        row=dict(type=kind,schema_version=1,session_id='synthetic',seq=len(rows),server_ns=t,**fields)
        if kind not in ('session','end','frame'):row['event_ns']=t
        rows.append(row);return row
    add('session',0,source='python',screen=dict(width_px=1200,height_px=800,width_cm=30,height_cm=20,measured=measured),conditions_fingerprint='same')
    revision=dict(matrix_revision=1) if versioned else {}
    add('calibration',1,action='reset' if uncalibrated else 'applied',matrix=None if uncalibrated else IDENTITY,
        calibration_id='nine',group='A' if uncalibrated else 'B',coordinate_system='full_screen_normalized',pipeline='python',
        error_radius_cm=2.,error_radius_source='matching_B_check',**revision)
    # Outside a check: never included even though calibrated and very long.
    add('frame',100_000_000,capture_ns=100_000_000,raw=[.99,.01],valid=True,status='tracking',frame_id=0,**revision)
    points=[[.12+(i%5)*.18,.15+(i//5)*.3] for i in range(14)]
    for i,point in enumerate(points):
        start=1_000_000_000+i*1_500_000_000
        add('target',start,action='appear' if i==0 else 'switch',task_type='evaluate',task_id='check-last',index=i,target_id=f'point-{i}',target=point)
        for j in range(24):
            t=start+int((.05+j*.05)*1e9)
            add('frame',t,capture_ns=t,raw=point,valid=True,status='tracking',frame_id=1+i*24+j,origin_m=[0,0,.5],**revision)
    end=22_000_000_000
    add('target',end,action='end',task_type='evaluate',task_id='check-last',index=13,target_id='point-13',target=points[-1],reason='completed')
    add('evaluation_summary',end+1,task_id='check-last',completed=True)
    add('end',end+2,written_lines=len(rows)+1,failed_records=0)
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    return rows


class SessionHeatmapTests(unittest.TestCase):
    def setUp(self):
        self.settings=replace(load_settings(use_local=False),heatmap=OPTIONS)

    def test_C_timeline_uses_same_math_and_preserves_coarse_group_in_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.jsonl';rows=recording(path)
            original=session_heatmap(path,self.settings)
            for row in rows:
                if row['type']=='calibration':row.update(group='C',error_radius_source='matching_C_check')
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            coarse=session_heatmap(path,self.settings)
            np.testing.assert_array_equal(coarse['grid'],original['grid'])
            self.assertEqual(coarse['regions'],original['regions']);self.assertTrue(coarse['coarse_calibrated'])
            self.assertTrue(all(p['context']['group']=='C' for p in coarse['phases']))

    def test_fourteen_targets_checks_only_readonly_and_neighborhood_fractions(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.jsonl';recording(path)
            before=path.read_bytes();result=session_heatmap(path,self.settings)
            self.assertEqual(result['completed_checks'],['check-last']);self.assertEqual(len(result['targets']),14)
            self.assertEqual(result['input_sha256'],hashlib.sha256(before).hexdigest());self.assertEqual(path.read_bytes(),before)
            self.assertAlmostEqual(result['grid'].sum(),result['regions']['total_fixation_s'])
            self.assertGreater(result['alignment']['exact_matrix_revision'],0)
            self.assertTrue(all(r['during_target_heat_fraction'] is not None and 0<r['during_target_heat_fraction']<=1 for r in result['targets']))
            self.assertGreater(result['smoothing'].get('zero_phase',0),0)
            self.assertTrue(all(r['kernel']['sigma_cm']==2. for r in result['targets']))
            first=result['targets'][0];height,width=result['grid'].shape
            x,y=np.meshgrid((np.arange(width)+.5)/width,(np.arange(height)+.5)/height)
            mask=((x-first['target'][0])/(2/30))**2+((y-first['target'][1])/(2/20))**2<=1
            self.assertAlmostEqual(first['whole_heat_fraction'],float(result['grid'][mask].sum()/result['grid'].sum()))

    def test_targets_only_change_validation_statistics_not_heatmap_or_fixations(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.jsonl';rows=recording(path);before=session_heatmap(path,self.settings)
            for row in rows:
                if row['type']=='target':row['target']=[.95,.95]
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            after=session_heatmap(path,self.settings)
            np.testing.assert_array_equal(before['grid'],after['grid'])
            self.assertEqual(before['regions'],after['regions'])
            self.assertNotEqual(before['targets'][0]['whole_heat_fraction'],after['targets'][0]['whole_heat_fraction'])

    def test_offline_near_edge_counts_dwell_and_target_heat_while_far_outside_is_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.jsonl';rows=recording(path)
            for row in rows:
                if row['type']=='target':
                    if row.get('target_id')=='point-0':row['target']=[.5,.05]
                    elif row.get('target_id')=='point-1':row['target']=[.5,.05]
                elif row['type']=='frame':
                    if 1_000_000_000<=row['capture_ns']<2_500_000_000:row['raw']=[.5,-.04]
                    elif 2_500_000_000<=row['capture_ns']<4_000_000_000:row['raw']=[.5,-.3]
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows));before=path.read_bytes()
            result=session_heatmap(path,self.settings)
            self.assertGreater(result['edge_clamped_s'],1.);self.assertGreater(result['outside_s'],1.)
            first,second=result['targets'][:2]
            self.assertGreater(first['during_target_fixation_s'],1.)
            self.assertGreater(first['during_target_heat_fraction'],0.)
            self.assertEqual(second['during_target_fixation_s'],0.)
            self.assertIsNone(second['during_target_heat_fraction'])
            self.assertAlmostEqual(result['grid'].sum(),result['regions']['total_fixation_s'])
            self.assertEqual(path.read_bytes(),before)

    def test_legacy_alignment_uncalibrated_and_missing_check_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.jsonl';recording(path,versioned=False)
            result=session_heatmap(path,self.settings)
            self.assertGreater(result['alignment']['legacy_event_time'],0)
            recording(path,uncalibrated=True)
            result=session_heatmap(path,self.settings)
            self.assertEqual(result['grid'].sum(),0.);self.assertGreater(result['skipped']['uncalibrated'],0)
            with self.assertRaisesRegex(ValueError,'没有完整'):session_heatmap(path,self.settings,task_id='absent')

    def test_version_lookup_beats_event_arrival_time_and_reset_or_conflict_rejects(self):
        events=[dict(type='calibration',server_ns=100,event_ns=100,matrix_revision=1,matrix=IDENTITY,
                     action='applied',group='B',coordinate_system='full_screen_normalized',pipeline='python')]
        row,source=CalibrationTimeline(events).resolve(dict(capture_ns=90,matrix_revision=1))
        self.assertIs(row,events[0]);self.assertEqual(source,'exact_matrix_revision')
        self.assertEqual(CalibrationTimeline(events).resolve(dict(capture_ns=90))[1],'missing_calibration_event')
        conflict=dict(events[0],matrix=[[1,0],[0,1],[.2,.2]])
        self.assertEqual(CalibrationTimeline(events+[conflict]).resolve(dict(capture_ns=110,matrix_revision=1))[1],'conflicting_matrix_revision')
        self.assertEqual(CalibrationTimeline(events).resolve(dict(capture_ns=110,matrix_revision=2))[1],'missing_matrix_revision')

    def test_offline_smoothing_never_bridges_blink_or_matrix_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'python.jsonl';rows=recording(path)
            blink=1_600_000_000;switch=11_500_000_000
            shifted=deepcopy(IDENTITY);shifted[2]=[.1,.1]
            event=dict(next(r for r in rows if r['type']=='calibration'),matrix=shifted,matrix_revision=2,
                       calibration_id='new-nine',event_ns=switch,server_ns=switch)
            for row in rows:
                if row['type']=='frame':
                    if row['capture_ns']==blink:row.update(valid=False,status='blink',raw=None)
                    elif row['capture_ns']>=switch:
                        row.update(matrix_revision=2,raw=[float(v-.1) for v in row['raw']])
            rows.insert(-1,event)
            for i,row in enumerate(rows):row['seq']=i
            rows[-1]['written_lines']=len(rows)
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            with patch('gaze_core.session_heatmap.zero_phase_smooth',wraps=zero_phase_smooth) as smooth:
                result=session_heatmap(path,self.settings)
            self.assertEqual({p['context']['matrix_revision'] for p in result['phases']},{1,2})
            for call in smooth.call_args_list:
                times=call.args[1]
                for boundary in (blink,switch):
                    relative=(boundary-1_000_000_000)/1e9
                    self.assertFalse(times[0]<relative<times[-1])
            self.assertEqual(result['skipped']['blink'],1)

    def test_recording_extension_keeps_numeric_whitelist_and_integer_revisions(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder=SessionRecorder(metadata(),settings=RecordingSettings(output_dir=folder,queue_size=10,close_timeout_s=3,request_timeout_s=10))
            data=dict(frame(),matrix_revision=np.int64(7))
            recorder.record_frame(data,capture_ns=100,inference_completed_ns=110,frame_id=1)
            recorder.emit('calibration',action='applied',matrix=IDENTITY,calibration_id='nine',group='B',matrix_revision=7,
                          error_radius_cm=np.float64(2.),error_radius_source='matching_B_check',event_ns=90,coordinate_system='full_screen_normalized')
            recorder.close();rows=[json.loads(line) for line in recorder.path.read_text().splitlines()]
            self.assertIs(type(rows[1]['matrix_revision']),int)
            with self.assertRaises(ValueError):validate_payload('calibration',dict(rows[2],image='base64'), 'python')
            with self.assertRaisesRegex(ValueError,'matrix_revision'):
                validate_payload('frame',dict(data,matrix_revision=True,capture_ns=100,inference_completed_ns=110),'python')

    def test_export_grid_json_png_report_and_cli_never_overwrite_input(self):
        root=ROOT/'data/heatmaps';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder,tempfile.TemporaryDirectory() as inputs:
            path=Path(inputs)/'python.jsonl';recording(path,versioned=False)
            settings=replace(self.settings,heatmap=replace(self.settings.heatmap,alpha_threshold=.25))
            result=session_heatmap(path,settings);destination=Path(folder)/'export'
            output=export_heatmap(result,directory=destination)
            self.assertGreater((output/'heatmap.png').stat().st_size,1000)
            meta=json.loads((output/'regions.json').read_text());self.assertEqual(len(meta['targets']),14)
            self.assertIn('edge_clamped_s',meta);self.assertIn('outside_s',meta)
            self.assertEqual(meta['visualization']['alpha_threshold'],.25)
            self.assertEqual(meta['visualization']['palette'],'transparent_yellow_red')
            with (output/'grid.csv').open() as stream:
                self.assertAlmostEqual(sum(float(r['seconds_per_cell']) for r in csv.DictReader(stream)),result['grid'].sum())
            report=(output/'report.md').read_text();self.assertIn('不表示「看了什么内容」',report);self.assertIn('旧录制缺矩阵版本号',report)
            self.assertIn('边缘容差内夹回的时长',report);self.assertIn('真正屏幕外的时长',report)
            self.assertIn('25%完全透明',report);self.assertIn('黄色至红色',report)
            self.assertIn('阈值到2倍阈值用smoothstep',report)
            with self.assertRaises(FileExistsError):export_heatmap(result,directory=destination)
            with self.assertRaisesRegex(ValueError,'data/heatmaps'):export_heatmap(result,directory=path)
            before=path.read_bytes()
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main([str(path),'--output',str(Path(folder)/'cli')]),0)
                self.assertEqual(main([str(path),'--task-id','missing','--output',str(Path(folder)/'bad')]),1)
            self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
