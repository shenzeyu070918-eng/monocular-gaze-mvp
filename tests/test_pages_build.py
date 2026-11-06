import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from tools.build_pages import build,ROOT

class PagesBuildTests(unittest.TestCase):
    def test_preview_closure_and_subpath(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'site';modules=build(target)
            self.assertIn('preview.mjs',modules)
            self.assertFalse({'app.mjs','entry.mjs'} & modules)
            self.assertNotIn('/assets/',(target/'galaxy/index.html').read_text())
            self.assertIn('./preview-config.json',(target/'galaxy/preview.mjs').read_text())
            self.assertEqual('BME',json.loads((target/'galaxy/preview-config.json').read_text())['text'])
            for p in target.rglob('*'):
                if p.is_file():self.assertNotIn(p.suffix,{'.jsonl','.task','.pt','.pth'})
            self.assertIn('合成',(target/'report.html').read_text())
            self.assertNotIn('/Users/',(target/'report.html').read_text())
    def test_reject_dirty_output(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'private.txt').write_text('must not publish')
            with self.assertRaises(ValueError):build(temp)
    def test_default_config_only(self):
        from gaze_core.config import load_settings
        with tempfile.TemporaryDirectory() as temp,patch('tools.build_pages.load_settings',wraps=load_settings) as call:
            build(Path(temp)/'site');call.assert_called_once_with(use_local=False)
if __name__=='__main__':unittest.main()
