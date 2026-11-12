from dataclasses import replace
import json,math,re,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from report_fixtures import session
from gaze_core.reporting.data import load_session,public_data,scanpath
from gaze_core.reporting.aggregate import aggregate
from gaze_core.reporting.build import build
from gaze_core.reporting.settings import ReportSettings
from gaze_core.attention.binding import Timeline


class ReportTests(unittest.TestCase):
    def test_full_offline_portable_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            p=session(root);before={str(x.relative_to(p)):x.read_bytes() for x in p.rglob('*') if x.is_file()}
            with patch('socket.getaddrinfo',side_effect=AssertionError('offline')):
                data=load_session(p);artifact=build(data)
            self.assertAlmostEqual(data['coverage'],1.0);self.assertEqual(data['scenes'][0]['stats']['regions'][0]['first_fixation_s'],0)
            content=artifact.html.read_text();self.assertIn('关键结论',content);self.assertIn('合成标题区',content);self.assertIn('注视路径',content);self.assertIn('data:image/png;base64,',content)
            self.assertNotRegex(content,r'(src|href)=[\"\x27]https?://');self.assertIn("connect-src 'none'",content);self.assertIn('@media print',content);self.assertIn('不做逐行逐字结论',content)
            for name,raw in before.items():self.assertEqual((p/name).read_bytes(),raw)
            self.assertEqual(json.loads((artifact.directory/'data.json').read_text())['source_version'],'original')
    def test_aggregate_equal_people_and_unseen_denominators(self):
        with tempfile.TemporaryDirectory() as root:
            a=load_session(session(root,'a','P01',x=.25));b=load_session(session(root,'b','P02',x=.75));c=load_session(session(root,'c','P03',x=.25))
            # Unequal heat totals cannot make a longer session dominate.
            b['scenes'][0]['grid']*=100
            combined=aggregate([a,b,c]);rows=combined['scenes'][0]['stats']['regions'];left=rows[0];right=rows[1]
            self.assertEqual((left['N_total'],left['N_valid'],left['N_seen']),(3,3,2));self.assertAlmostEqual(left['seen_fraction'],2/3);self.assertEqual(left['first_fixation_s'],0)
            self.assertAlmostEqual(right['hard_fraction'],1/3);self.assertAlmostEqual(combined['scenes'][0]['grid'].sum(),1)
            single=aggregate([a]);self.assertIsNone(single['scenes'][0]['stats']['regions'][1]['first_fixation_s']);self.assertEqual(single['scenes'][0]['scanpath'],[])
            artifact=build(single);self.assertIn('仅一人数据',artifact.html.read_text())
            with self.assertRaisesRegex(ValueError,'重复'):aggregate([a,a])
            b['metadata']['stimulus']['sha256']='different'
            with self.assertRaisesRegex(ValueError,'SHA256'):aggregate([a,b])
    def test_legacy_mapping_and_canonical_aoi(self):
        with tempfile.TemporaryDirectory() as root:
            a=load_session(session(root,'a',None));b=load_session(session(root,'b','P02'))
            b['scenes'][0]['regions'][0]['id']='other-local-ocr-id'
            with self.assertRaisesRegex(ValueError,'映射'):aggregate([a,b])
            result=aggregate([a,b],participant_map={'a':'P01'})
            self.assertEqual(result['scenes'][0]['stats']['regions'][0]['N_seen'],2)
            row=result['scenes'][0]['stats'];total=sum(r['soft_fraction'] for r in row['regions']);self.assertLessEqual(total,1.0)
    def test_scanpath_gaps_merge_numpy_and_area(self):
        def seg(a,b,center,**kw):return dict(start_ns=int(a*1e9),end_ns=int(b*1e9),center=center,scene_id='a',excluded_reason=None,**kw)
        values=[seg(0,1,[np.float64(.2),.3]),seg(1.1,3.1,[.201,.3]),dict(seg(3.1,3.2,[.2,.3]),excluded_reason='blink'),seg(3.2,4.2,[.3,.3])]
        scale=dict(sigma_x=.1,sigma_y=.1)
        nodes,total=scanpath(values,scale,ReportSettings())
        self.assertEqual(total,3);self.assertTrue(nodes[1]['connect']);self.assertFalse(nodes[2]['connect']);self.assertEqual(nodes[1]['duration_s'],2)
        merged,total=scanpath(values,scale,ReportSettings(scanpath_merge_sigma=.5));self.assertEqual(total,2);self.assertEqual(merged[0]['duration_s'],3)
        timeline=Timeline([dict(type='scene',scene_id='a',start_ns=0),dict(type='state',time_ns=1020000000,status='user_paused'),dict(type='state',time_ns=1080000000,status='recording')],0,5_000_000_000)
        nodes,_=scanpath(values,scale,ReportSettings(),timeline=timeline);self.assertFalse(nodes[1]['connect'])
    def test_bad_hash_truncated_and_explicit_derived(self):
        with tempfile.TemporaryDirectory() as root:
            p=session(root)
            with (p/'fixations.jsonl').open('a') as f:f.write('{broken')
            self.assertTrue(any('截断' in w for w in load_session(p)['warnings']))
            import shutil
            for name in ['stats','heatmaps'] :shutil.copytree(p/name,p/'derived'/'recovered'/name)
            for name in ['aoi.json','fixations.jsonl']:shutil.copy(p/name,p/'derived'/'recovered'/name)
            self.assertEqual(load_session(p,derived='derived/recovered')['source_version'],'derived/recovered')
            (p/'scenes/a.png').write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError,'校验失败'):load_session(p)
    def test_empty_and_excluded_timeline(self):
        with tempfile.TemporaryDirectory() as root:
            p=session(root);data=load_session(p)
            self.assertFalse(data['scenes'][0]['stats']['regions'][1]['hard_s'])
            summary=data['summary'];summary['valid_bound_fixation_s']=0
            (p/'stats/session.json').write_text(json.dumps(summary))
            data=load_session(p);self.assertEqual(data['coverage'],0)
            # Unsafe file references, including symlinks, fail closed.
            (p/'derived-link').symlink_to(Path(root))
            with self.assertRaises(ValueError):load_session(p,derived='derived-link')

    def test_optional_pdf_failure_retains_html_and_success_uses_local_profile(self):
        from gaze_core.reporting.pdf import export_pdf
        from types import SimpleNamespace as NS
        with tempfile.TemporaryDirectory() as root:
            artifact=build(load_session(session(root)));original=artifact.html.read_bytes()
            with patch('overlay.galaxy_browser.chrome_executable',return_value=None):
                with self.assertRaisesRegex(RuntimeError,'Chrome'):export_pdf(artifact)
            def runner(args,**kwargs):
                self.assertTrue(any(arg.startswith('--user-data-dir=') for arg in args));self.assertIn('--disable-background-networking',args)
                self.assertEqual(args[-1],artifact.html.as_uri())
                target=next(arg.split('=',1)[1] for arg in args if arg.startswith('--print-to-pdf='))
                Path(target).write_bytes(b'%PDF-synthetic')
                return NS(returncode=0)
            pdf=export_pdf(artifact,executable='/fake/chrome',runner=runner)
            self.assertTrue(pdf.exists());self.assertEqual(artifact.html.read_bytes(),original)
            with self.assertRaisesRegex(RuntimeError,'未生成PDF'):
                export_pdf(artifact,executable='/fake/chrome',runner=lambda *a,**kw:NS(returncode=0))
            self.assertEqual(pdf.read_bytes(),b'%PDF-synthetic')


class AOIDisplaySizeTests(unittest.TestCase):
    def test_render_uses_displayed_image_size_and_keeps_small_manual_aoi(self):
        with tempfile.TemporaryDirectory() as root:
            data = load_session(session(root))
            metadata = data['metadata']
            metadata['geometry'] = dict(measured=True, width_cm=30, height_cm=20)
            metadata['stimulus']['screen_rect'] = [.25, .1, .75, .9]
            scene = data['scenes'][0]
            region = scene['regions'][0]
            region['rect'] = [0, 0, .05, .5]
            content = build(data).html.read_text()
            self.assertIn('0.75 × 8.00 cm（宽 × 高）', content)
            self.assertIn('手动</small><small class="provenance">0.75 × 8.00 cm（宽 × 高） · 小于误差尺度，区域结论不可靠', content)
            metadata['geometry']['measured'] = False
            self.assertIn('物理尺寸未记录', build(data).html.read_text())

    def test_render_explains_automatic_merge_using_original_boxes(self):
        with tempfile.TemporaryDirectory() as root:
            data = load_session(session(root))
            scene = data['scenes'][0]
            region = scene['regions'][0]
            region.update(source='auto_merge', merged_ids=['old'], minimum_size=[.08, .12])
            scene['regions'].append(dict(id='old', role='child', rect=[0, 0, .03, .4]))
            self.assertIn('自动合并：候选区域低于误差尺度对应的最小尺寸', build(data).html.read_text())
            scene['regions'][-1]['rect'] = [0, 0, .1, .4]
            self.assertIn('自动合并：主区域数量上限', build(data).html.read_text())
