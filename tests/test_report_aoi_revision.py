import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from report_fixtures import session
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build
from gaze_core.reporting.aggregate import aggregate
from gaze_core.reporting.render import cards,render_html
from gaze_core.reporting.editor import render_editor,rebuild,EditorBridge
from gaze_core.reporting.settings import ReportSettings
from gaze_core.attention.manual_aoi import save_template


class AOIRevisionTests(unittest.TestCase):
    def test_short_labels_scene_scope_and_screen_overview(self):
        with tempfile.TemporaryDirectory() as root:
            data=load_session(session(root,mode='screen'));scene=data['scenes'][0]
            self.assertLessEqual(len(scene['stats']['regions'][0]['label']),20)
            scenes=[]
            for i,time in enumerate([.2,2.,5.,4.,3.]):
                s=copy.deepcopy(scene);s['id']=str(i);s['stats']['valid_fixation_s']=time;scenes.append(s)
            scenes[1]['app']=None;data['scenes']=scenes;body=render_html(data)
            self.assertIn('场景总览',body);self.assertEqual(body.count('class="scene-detail"'),4);self.assertEqual(body.count('class="scene-detail" id="scene-') ,4)
            self.assertEqual(body.count('" open>'),3);self.assertNotIn('id="scene-0"',body)
            self.assertNotIn('图片刺激物',body);self.assertIn('应用名未记录',body)
            self.assertIn('（停留最久场景 2）',cards(data)[-1][0]);self.assertEqual(cards(data)[-1][1],'1 个')
            self.assertLess(body.index('href="#scene-2"'),body.index('href="#scene-3"'))
    def test_template_reused_in_old_sessions_and_aggregate(self):
        with tempfile.TemporaryDirectory() as root:
            a=session(root,'a','P01');b=session(root,'b','P02');digest=hashlib.sha256((a/'scenes/a.png').read_bytes()).hexdigest()
            for p in [a,b]:
                m=json.loads((p/'session.json').read_text());m['stimulus']['sha256']=digest;(p/'session.json').write_text(json.dumps(m))
            save_template(root,digest,[dict(id='poster',name='整张海报',rect=[0,0,1,1])])
            datasets=[load_session(a),load_session(b)]
            for d in datasets:
                self.assertEqual(d['scenes'][0]['stats']['regions'][0]['label'],'整张海报');self.assertEqual(d['scenes'][0]['aoi_source'],'manual');self.assertGreater(d['scenes'][0]['stats']['regions'][0]['hard_s'],0)
            result=aggregate(datasets);self.assertEqual(result['scenes'][0]['stats']['regions'][0]['N_seen'],2);self.assertEqual(result['scenes'][0]['stats']['regions'][0]['label'],'整张海报')
            save_template(root,digest,[]);self.assertEqual(load_session(a)['scenes'][0]['stats']['regions'],[])
    def test_editor_single_file_json_escape_and_rebuild_preserves_sources(self):
        with tempfile.TemporaryDirectory() as root:
            path=session(root,mode='screen');artifact=build(load_session(path));before={str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file() and 'reports' not in p.relative_to(path).parts}
            artifact.data['scenes'][0]['regions'][0]['name']='</script><img src=x>'
            page=render_editor(artifact.data,options=artifact.options);self.assertIn('data:image/png;base64,',page);self.assertNotIn('<img src=x>',page);self.assertNotIn('type="module"',page);self.assertNotIn('src="http',page)
            doc=dict(schema_version=2,coordinate_space='full_screen_normalized',scenes={'a':[dict(id='body',name='正文与图片',rect=[0,0,1,1])]})
            new=rebuild(artifact,doc);self.assertNotEqual(new.html,artifact.html);self.assertEqual(new.data['scenes'][0]['stats']['regions'][0]['label'],'正文与图片')
            self.assertTrue(new.data['source_version'].startswith('derived/'));self.assertIn('手动',new.html.read_text());self.assertIn('hierarchical-v2',new.html.read_text())
            for name,raw in before.items():self.assertEqual((path/name).read_bytes(),raw)
            deleted=rebuild(new,dict(doc,scenes={'a':[]}));self.assertEqual(deleted.data['scenes'][0]['stats']['regions'],[])
    def test_loopback_auth_origins_and_save_regenerate(self):
        import urllib.request,urllib.error
        with tempfile.TemporaryDirectory() as root:
            artifact=build(load_session(session(root,mode='screen')));bridge=EditorBridge(artifact)
            try:
                self.assertTrue(bridge.url.startswith('http://127.0.0.1:'))
                with urllib.request.urlopen(bridge.url) as r:self.assertIn('编辑关注区域',r.read().decode())
                def request(payload,token,origin=None):
                    headers={'Content-Type':'application/json','X-AOI-Token':token}
                    if origin:headers['Origin']=origin
                    return urllib.request.Request(bridge.endpoint+'/save',json.dumps(payload).encode(),headers=headers)
                payload=dict(document=dict(schema_version=2,coordinate_space='full_screen_normalized',scenes={'a':[]}),save_template=False)
                for token,origin in [('wrong',None),(bridge.token,'https://foreign.invalid')]:
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request(payload,token,origin))
                    self.assertEqual(error.exception.code,403)
                with urllib.request.urlopen(request(payload,bridge.token,bridge.origin)) as r:value=json.load(r)
                self.assertEqual(bridge.revision,1);self.assertTrue(value['derived'].startswith('derived/'));self.assertEqual(bridge.artifact.data['scenes'][0]['stats']['regions'],[])
                with urllib.request.urlopen(value['report_url']) as r:self.assertIn('关键结论',r.read().decode())
            finally:bridge.close()

    def test_legacy_text_only_cache_is_rebuilt_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            path=session(root);aoi=dict(schema_version=1,scenes={'a':dict(regions=[dict(id='old-text',rect=[.1,.1,.3,.2],source='ocr',text='旧文字')],ocr_lines=[dict(rect=[.1,.1,.3,.2],text='旧文字',confidence=.9)])})
            (path/'aoi.json').write_text(json.dumps(aoi));stats=json.loads((path/'stats/a.json').read_text());stats['regions']=[dict(id='old-text',hard_s=0,soft_s=0,first_fixation_ns=None,entries=0,fixations=0,hard_fraction=0,soft_fraction=0)];(path/'stats/a.json').write_text(json.dumps(stats))
            before={name:(path/name).read_bytes() for name in ['aoi.json','stats/a.json','fixations.jsonl','session.json']}
            data=load_session(path);self.assertGreater(sum(r['hard_s'] for r in data['scenes'][0]['stats']['regions']),0);self.assertIn('report-view',data['scenes'][0]['aoi_algorithm_version']);self.assertTrue(any('重建' in w for w in data['warnings']))
            for name,raw in before.items():self.assertEqual((path/name).read_bytes(),raw)
    def test_editor_rejects_non_rectangle_payloads(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=build(load_session(session(root,mode='screen')))
            bad=dict(schema_version=2,coordinate_space='full_screen_normalized',scenes={'a':[dict(id='bad',name='区域',rect=[0,0,1,1],image='forbidden-base64')]})
            with self.assertRaisesRegex(ValueError,'图像'):rebuild(artifact,bad)
            self.assertFalse((Path(artifact.data['source_root'])/'derived').exists())

    def test_empty_session_keeps_statistical_report_and_explains_editor(self):
        with tempfile.TemporaryDirectory() as root:
            data=load_session(session(root));data['scenes']=[]
            artifact=build(data);self.assertIn('关键结论',artifact.html.read_text());self.assertIn('暂无可编辑', (artifact.directory/'aoi-editor.html').read_text())
