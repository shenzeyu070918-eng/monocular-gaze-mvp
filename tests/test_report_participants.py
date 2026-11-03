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
