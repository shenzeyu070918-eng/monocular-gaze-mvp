import json,tempfile,unittest
from pathlib import Path
from gaze_core.reporting.participants import next_participant_id,participant_id
from gaze_core.reporting.settings import ReportSettings
from gaze_core.config import load_settings


class Participants(unittest.TestCase):
    def test_increment_and_edit(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(next_participant_id(root),'P01')
            for i,name in enumerate(['P02','P09','custom']):
                p=Path(root)/str(i);p.mkdir();(p/'session.json').write_text(json.dumps({'participant_id':name}))
            self.assertEqual(next_participant_id(root),'P10')
            self.assertEqual(participant_id('custom-1'),'custom-1')
            with self.assertRaises(ValueError):participant_id('../name')
    def test_configuration(self):
        self.assertTrue(load_settings(use_local=False).report.auto_stimulus)
        with self.assertRaises(ValueError):ReportSettings(ai_timeout_s=31)

    def test_new_stimulus_metadata_saved_with_anonymous_id(self):
        from gaze_core.attention.session import AttentionSession
        from overlay.mapping import ScreenRect
        from gaze_core.profiles import PYTHON_COORDINATE_VERSION
        import time,hashlib
        from PIL import Image
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'image.png';Image.new('RGB',(40,30),'white').save(path)
            profile=dict(pipeline='python',source='nine_point',group='B',coordinate_version=PYTHON_COORDINATE_VERSION,conditions={},matrix=[[1,0],[0,1],[0,0]])
            scale=dict(sigma_x=.1,sigma_y=.1,sigma_cm=2.,source='configured_cm')
            worker=AttentionSession(load_settings(use_local=False),ScreenRect(0,0,640,480),root=Path(root)/'sessions',processor=lambda *a,**k:{})
            try:
                worker.start(profile,{},scale,mode='stimulus',participant_id='P17',stimulus=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),screen_rect=[0,0,1,1],kernel=scale))
                end=time.monotonic()+2
                while worker.state=='starting' and time.monotonic()<end:time.sleep(.01)
                self.assertEqual(worker.state,'recording',worker.error)
                self.assertEqual(json.loads((worker.path/'session.json').read_text())['participant_id'],'P17')
            finally:worker.close()
