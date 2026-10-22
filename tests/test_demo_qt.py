"""Synthetic Demo UI only: no camera, speech playback or injected global input."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt,QObject,Signal,QRect,QProcess
from PySide6.QtGui import QImage,QKeyEvent
from PySide6.QtWidgets import QApplication
from gaze_core.config import load_settings,DemoSettings,OverlaySettings
from gaze_core.capture import Prediction
from gaze_core.demo import DemoRun
from gaze_core.recorder import validate_payload
from overlay.demo_session import DemoSession
from overlay.demo_window import DemoWindow
from overlay.mapping import ScreenRect
from overlay.state import RingView
from overlay.window import OverlayWindow
from overlay.speech import SystemSpeech


class FakeWindow:
    def __init__(self,run,*args,**kwargs):
        self.run=run;self.cancelled=Mock();self.open=Mock();self.dispose=Mock();self.refresh=Mock()


class DemoQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)

    def test_interactive_flags_font_paint_ack_and_escape(self):
        run=DemoRun('board',DemoSettings(),1512,982,(90,90),group='B')
        screen=self.app.primaryScreen()
        window=DemoWindow(run,screen,run.options,clock_ns=lambda:123)
        window.resize(1512,982)
        try:
            self.assertFalse(window.windowFlags()&Qt.WindowType.WindowTransparentForInput)
            self.assertGreaterEqual(window.button_font.pixelSize(),40)
            self.assertIsNone(run.presented_ns)
            image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0);window.render(image)
            self.assertEqual(run.presented_ns,123)
            self.assertEqual(image.pixelColor(0,0).name(),run.options.background.lower())
            cancelled=Mock();window.cancelled.connect(cancelled)
            window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress,Qt.Key.Key_Escape,Qt.KeyboardModifier.NoModifier))
            cancelled.assert_called_once_with('escape')
            window.refresh((500,500),.65,123)
            with patch.object(window,'update') as update:
                window.refresh((500,500),.65,123);update.assert_not_called()
        finally:window.dispose();self.app.processEvents()

    def make_session(self,*,group='B',pipeline='python',recording=False,practice=False):
        now=[0];settings=load_settings(use_local=False)
        settings=replace(settings,demo=replace(settings.demo,practice=practice))
        s=SimpleNamespace(settings=settings,active=False,clicks=None,display_revision=1,_active_group=group,
                          profiles=SimpleNamespace(busy=False,conditions={'camera':1},profile={'pipeline':pipeline}),
                          controller=SimpleNamespace(snapshot=lambda:SimpleNamespace(status='running')),radius=None,
                          set_demo_active=Mock(),recording=Mock())
        s.recording.status.return_value=dict(active=recording,session_id='file1')
        overlay=Mock();overlay.rect_info=ScreenRect(0,0,1512,982,2);overlay.isVisible.return_value=False
        panel=Mock();panel.isVisible.return_value=False
        manager=DemoSession(s,overlay,panel,notice=Mock(),window_factory=FakeWindow,clock_ns=lambda:now[0])
        return manager,s,overlay,panel,now

    def feed_selection(self,manager,now):
        run=manager.run;run.presented(0)
        x,y,w,h=run.buttons[0].rect
        for i,ms in enumerate(range(0,1001,50)):
            now[0]=ms*1_000_000
            prediction=Prediction(i,now[0],now[0]+999_999_999,{'raw':[.9,.9]})
            display=RingView(((x+w/2)/1511,(y+h/2)/981),.65,'tracking',True)
            manager.tick(prediction,display,now[0],active=True,eligible=True)

    def test_filtered_coordinate_optional_recording_and_restore_hidden_state(self):
        manager,s,overlay,panel,now=self.make_session(recording=False)
        manager.speech_factory=Mock(side_effect=RuntimeError('no speech'))
        manager.start('board',None);self.feed_selection(manager,now)
        self.assertEqual(manager.run.feedback_text,'我想喝水')
        self.assertEqual(manager.run.selections[0]['dwell_ms'],1000)
        self.assertTrue(manager.run.speech_failed)
        s.recording.emit.assert_not_called();s.recording.manual.assert_not_called()
        now[0]=1_500_000_000;manager.cancel()
        s.set_demo_active.assert_any_call(True);s.set_demo_active.assert_called_with(False)
        overlay.setVisible.assert_called_with(False);panel.hide.assert_called();panel.present.assert_not_called()

    def test_numeric_events_validate_and_no_text_images_or_raw_mutation(self):
        manager,s,overlay,panel,now=self.make_session(recording=True)
        manager.speech_factory=Mock(side_effect=RuntimeError('no speech'))
        manager.start('board',None);self.feed_selection(manager,now)
        now[0]=1_500_000_000;manager.cancel()
        events=[]
        for call in s.recording.emit.call_args_list:
            kind=call.args[0];fields=call.kwargs;validate_payload(kind,fields,'python');events.append((kind,fields))
            self.assertNotIn('text',fields);self.assertNotIn('raw',fields)
            for forbidden in ('image','base64','screen_content','keys'):
                with self.assertRaises(ValueError):validate_payload(kind,dict(fields,**{forbidden:'bad'}),'python')
            if fields.get('action') in {'confirmed','undo'}:
                with self.assertRaises(ValueError):validate_payload(kind,dict(fields,target='data:image;base64,bad'),'python')
        self.assertEqual([e[1].get('action') for e in events[:2]],['triggered','undo'])
        self.assertEqual(events[-1][1]['false_activation_count'],1)

    def test_missing_or_web_profile_refused_C_accepted_context_change_closes(self):
        for group,pipeline in (('A','python'),('B','web')):
            manager,*_=self.make_session(group=group,pipeline=pipeline)
            with self.assertRaisesRegex(ValueError,'校准'):manager.start('board',None)
        manager,s,overlay,panel,now=self.make_session(group='C')
        manager.start('game',None);s.display_revision=2
        manager.tick(None,RingView(None,0,'',False),0,active=True,eligible=False)
        self.assertFalse(manager.active)

    def test_recording_session_switch_does_not_emit_orphan_undo(self):
        manager,s,overlay,panel,now=self.make_session(recording=True)
        manager.speech_factory=Mock(side_effect=RuntimeError('no speech'))
        manager.start('board',None);self.feed_selection(manager,now)
        s.recording.status.return_value=dict(active=True,session_id='file2')
        now[0]=1_500_000_000;manager.cancel()
        self.assertEqual(sum(call.kwargs.get('action')=='undo' for call in s.recording.emit.call_args_list),0)

    def test_open_control_panel_freezes_demo_selection(self):
        manager,s,overlay,panel,now=self.make_session()
        manager.start('board',None);panel.isVisible.return_value=True
        self.feed_selection(manager,now)
        self.assertFalse(manager.run.selections)
        manager.cancel()

    def test_presentation_persistence_restore_and_success_notice(self):
        now=[0.]
        window=OverlayWindow(ScreenRect(0,0,1512,982),OverlaySettings(),clock=lambda:now[0])
        try:
            window.set_presentation(True);now[0]=100;window._refresh_label()
            self.assertEqual(window._label_opacity,1);self.assertEqual(window.font().pixelSize(),26)
            self.assertFalse(window._label_timer.isActive())
            with patch('overlay.window.QTimer.singleShot') as later:
                window.show_calibration_notice();later.assert_called_once()
                self.assertEqual(later.call_args.args[0],3000)
                notice=window._completion_notice;self.assertEqual(notice.text(),'校准完成')
                later.call_args.args[1]();self.assertFalse(notice.isVisible())
            window.set_presentation(False);now[0]=106;window._refresh_label()
            self.assertEqual(window._label_opacity,0);self.assertEqual(window.font().pixelSize(),13)
        finally:window.close();window.deleteLater();self.app.processEvents()


class FakeProcess(QObject):
    finished=Signal(int,object);errorOccurred=Signal(object);started=Signal()
    def __init__(self,parent):
        super().__init__(parent);self.bytes=None;self.command=None;self.killed=False
    def setStandardOutputFile(self,_):pass
    def setStandardErrorFile(self,_):pass
    def start(self,program,args):self.command=(program,args);self.started.emit()
    def write(self,value):self.bytes=value
    def closeWriteChannel(self):pass
    def kill(self):self.killed=True
    def state(self):return QProcess.ProcessState.NotRunning


class SpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([]);cls.app.setQuitOnLastWindowClosed(False)

    def test_native_platform_commands_stdin_and_failure(self):
        for platform in ('darwin','win32'):
            speaker=SystemSpeech(platform=platform,process_factory=FakeProcess)
            done=Mock();speaker.finished.connect(done)
            speaker.speak('我想喝水; $(echo not-a-command)')
            process=speaker.process
            self.assertEqual(process.bytes.decode(),'我想喝水; $(echo not-a-command)')
            self.assertNotIn('我想喝水',str(process.command))
            process.finished.emit(1,QProcess.ExitStatus.CrashExit)
            done.assert_called_once_with(False);self.assertIsNone(speaker.process)

    def test_stop_cancels_without_reporting_late_completion(self):
        speaker=SystemSpeech(platform='darwin',process_factory=FakeProcess);done=Mock();speaker.finished.connect(done)
        speaker.speak('是');process=speaker.process;speaker.stop()
        self.assertTrue(process.killed);process.finished.emit(0,QProcess.ExitStatus.NormalExit);done.assert_not_called()


if __name__=='__main__':unittest.main()
