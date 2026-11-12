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
from gaze_core.reporting.ai import prepare
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
                self.assertEqual(window.prepared.selected,('a',));self.assertFalse(window.ocr.isChecked());self.assertIn('DeepSeek',window.status.text());self.assertIn('deepseek-flash',window.status.text());self.assertIn('max_tokens',window.fields.toPlainText())
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

    def test_editor_rebuild_updates_current_report_and_deletion_closes_helper(self):
        with tempfile.TemporaryDirectory() as root:
            attention,ui=self.controls(root)
            try:
                path=session(root);old=build(load_session(path));new=build(load_session(path));ui.artifact=old
                class Editor:pass
                editor=Editor();editor.revision=1;editor.artifact=new;editor.lock=Mock();editor.lock.locked.return_value=False;editor.close=Mock()
                ui.editors=[editor];ui.poll();self.assertIs(ui.artifact,new);self.assertIn('新派生版本',attention.controls.panel.report_status.text())
                ui.cancel_editors(path);editor.close.assert_called_once();self.assertEqual(ui.editors,[])
            finally:ui.close();attention.controls.panel.deleteLater()


    def test_text_only_checkbox_and_automatic_model_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            artifact=build(load_session(session(root)));window=SendPreview(artifact)
            try:
                window.text_only.setChecked(True);self.assertTrue(window.prepared.text_only);self.assertEqual(window.prepared.thumbnails,());self.assertFalse(window.ocr.isEnabled());self.assertIn('不发图片',window.warning.text());self.assertIn('DeepSeek',window.status.text())
            finally:window.close();window.deleteLater()
            artifact.options=replace(artifact.options,model='deepseek-v4-pro');window=SendPreview(artifact)
            try:
                self.assertTrue(window.text_only.isChecked());self.assertFalse(window.text_only.isEnabled());self.assertTrue(window.prepared.text_only);self.assertIn('自动',window.status.text())
            finally:window.close();window.deleteLater()

    def test_finalizing_screen_click_is_queued_and_pinned_to_that_session(self):
        with tempfile.TemporaryDirectory() as root:
            screen=session(root,name='20261112T111601.902210Z-b8a7a2f6',mode='screen',identity=None)
            other=session(root,name='20261112T111854.132083Z-de2ad10f')
            attention,ui=self.controls(root,screen)
            try:
                panel=attention.controls.panel
                attention.service.state='finalizing';attention.service.mode='screen';attention.service.busy=True
                ui.poll()
                with patch('overlay.reporting.QDesktopServices.openUrl',return_value=True) as opened:
                    ui.generate();ui.generate()
                    self.assertIn('正在整理',panel.report_status.text())
                    self.assertFalse(panel.buttons['report_generate'].isEnabled())
                    self.assertFalse(panel.report_session.isEnabled())
                    self.assertIsNone(ui.future)
                    attention.service.state='ended';attention.service.busy=False
                    ui.poll();self.wait(ui)
                    self.assertEqual(Path(ui.artifact.data['source_root']),screen)
                    self.assertIn('已在浏览器打开',panel.report_status.text())
                    self.assertIn('b8a7a2f6',panel.report_status.text())
                    opened.assert_called_once()
                    attention.notice.assert_called_once()
                    self.assertFalse((other/'reports').exists())
            finally:ui.close();panel.deleteLater()

    def test_select_recent_screen_instead_of_service_path_and_surface_failure(self):
        with tempfile.TemporaryDirectory() as root:
            screen=session(root,name='screen-old',mode='screen',identity=None)
            other=session(root,name='stimulus-new')
            attention,ui=self.controls(root,other)
            panel=attention.controls.panel
            try:
                panel.report_session.setCurrentIndex(panel.report_session.findData(str(screen)))
                with patch('overlay.reporting.build',side_effect=OSError('磁盘不可写')) as builder:
                    ui.generate()
                    self.assertIn('正在生成报告',panel.report_status.text())
                    self.assertFalse(panel.buttons['report_generate'].isEnabled())
                    self.wait(ui)
                    self.assertEqual(Path(builder.call_args.args[0]['source_root']),screen)
                    self.assertIn('磁盘不可写',panel.report_status.text())
                    self.assertTrue(panel.buttons['report_generate'].isEnabled())
                    self.assertFalse((other/'reports').exists())
            finally:ui.close();panel.deleteLater()

    def test_pdf_and_ai_feedback_disable_actions_and_restore_after_completion(self):
        with tempfile.TemporaryDirectory() as root:
            attention,ui=self.controls(root)
            panel=attention.controls.panel
            try:
                ui.artifact=build(load_session(session(root)))
                pdf=ui.artifact.directory/'report.pdf'
                with patch('gaze_core.reporting.pdf.export_pdf',return_value=pdf),patch('overlay.reporting.QDesktopServices.openUrl',return_value=True) as opened:
                    ui.pdf()
                    self.assertIn('正在导出 PDF',panel.report_status.text())
                    self.assertFalse(panel.buttons['report_pdf'].isEnabled())
                    self.wait(ui)
                    self.assertIn('PDF 已导出并打开',panel.report_status.text())
                    opened.assert_called_once()
                with patch('overlay.reporting.SendPreview') as preview,patch('overlay.reporting.interpret',return_value={'ok':False,'reason':'HTTP 402，余额不足'}):
                    preview.return_value.prepared=prepare(ui.artifact,['a'])
                    preview.return_value.exec.return_value=QDialog.DialogCode.Accepted
                    ui.ai()
                    self.assertIn('正在AI 解读',panel.report_status.text())
                    self.assertFalse(panel.buttons['report_ai'].isEnabled())
                    self.wait(ui)
                    self.assertIn('HTTP 402',panel.report_status.text())
                    self.assertTrue(panel.buttons['report_ai'].isEnabled())
            finally:ui.close();panel.deleteLater()
