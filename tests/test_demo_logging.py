import io
from pathlib import Path
import tempfile
import unittest
from gaze_core.config import load_settings
from overlay.startup_log import StartupLog


class StartupLogTests(unittest.TestCase):
    def test_configuration_sources_saved_by_default_verbose_restores_terminal(self):
        settings=load_settings(use_local=False)
        with tempfile.TemporaryDirectory() as directory:
            for verbose in (False,True):
                output=io.StringIO()
                with StartupLog(verbose=verbose,directory=directory,stream=output) as log:
                    log.configuration(settings);log.detail('tray.geometry()=test');log.key('设备和摄像头')
                    path=log.path
                content=path.read_text(encoding='utf-8')
                self.assertIn(settings.config_sources[0],content);self.assertIn('tray.geometry()',content)
                self.assertEqual('实际配置' in output.getvalue(),verbose)
                self.assertEqual('tray.geometry()' in output.getvalue(),verbose)
                self.assertEqual(output.getvalue().count('设备和摄像头'),1)
                self.assertNotIn('我想喝水',content)

    def test_unwritable_log_does_not_prevent_overlay_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'not-a-directory';file.write_text('x')
            output=io.StringIO()
            with StartupLog(directory=file,stream=output) as log:
                log.configuration(load_settings(use_local=False));log.key('设备')
            self.assertIn('无法写入',output.getvalue());self.assertIn('设备',output.getvalue())


if __name__=='__main__':unittest.main()
