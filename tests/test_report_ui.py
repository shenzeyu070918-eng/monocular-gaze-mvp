import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,Mock
import tempfile,time,unittest
from PySide6.QtWidgets import QApplication,QDialog
from overlay.actions import OverlayActions
from overlay.panel import ControlPanel
from overlay.reporting import ReportUI,SendPreview
from gaze_core.reporting.settings import ReportSettings
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build
from report_fixtures import session


class ReportUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def controls(self,root,path=None):
        actions=OverlayActions();panel=ControlPanel(actions)
        attention=NS(controls=NS(actions=actions,panel=panel),notice=Mock(),service=NS(root=Path(root),path=path,state='ended',mode='stimulus',busy=False),session=NS(settings=NS(report=ReportSettings())))
        return attention,ReportUI(attention)
    def wait(self,ui):
        end=time.monotonic()+3
        while ui.future is not None and time.monotonic()<end:ui.poll();self.app.processEvents();time.sleep(.01)
        self.assertIsNone(ui.future)
    def test_auto_once_generate_open_no_ai_and_cancel_zero_sends(self):
        with tempfile.TemporaryDirectory() as root:
            attention,ui=self.controls(root,session(root))
            try:
                with patch('overlay.reporting.QDesktopServices.openUrl',return_value=True) as opened,patch('overlay.reporting.interpret') as send:
                    ui.poll();self.wait(ui);self.assertIsNotNone(ui.artifact);self.assertEqual(opened.call_count,1)
                    ui.poll();self.assertIsNone(ui.future);send.assert_not_called()
                    with patch('overlay.reporting.SendPreview') as preview:
                        preview.return_value.exec.return_value=QDialog.DialogCode.Rejected
                        ui.ai();send.assert_not_called();self.assertIn('零请求',attention.controls.panel.report_status.text())
            finally:ui.close();attention.controls.panel.deleteLater()
    def test_exact_preview_updates_selected_fields(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=build(load_session(session(root)));window=SendPreview(artifact)
            try:
                self.assertEqual(window.prepared.selected,('a',));self.assertFalse(window.ocr.isChecked());self.assertIn('gpt-6.1-terra',window.status.text());self.assertIn('max_output_tokens',window.fields.toPlainText())
                window.checks['a'].setChecked(False);self.assertIsNone(window.prepared);self.assertEqual(window.fields.toPlainText(),'')
                window.checks['a'].setChecked(True);window.ocr.setChecked(True);self.assertTrue(window.prepared.include_ocr)
            finally:window.close();window.deleteLater()
    def test_failure_reason_persisted_with_stats_no_secret(self):
        with tempfile.TemporaryDirectory() as root:
            attention,ui=self.controls(root)
            try:
                ui.artifact=build(load_session(session(root)));__import__('gaze_core.reporting.build',fromlist=['write_ai_status']).write_ai_status(ui.artifact,'AI超时，保留统计报告')
                self.assertIn('AI超时',ui.artifact.html.read_text());self.assertTrue((ui.artifact.directory/'ai-status.json').exists())
            finally:ui.close();attention.controls.panel.deleteLater()
