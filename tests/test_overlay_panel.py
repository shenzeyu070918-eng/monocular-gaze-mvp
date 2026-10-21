"""Shared controls and result presentation offscreen; no camera or input hook."""
from copy import deepcopy
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import io
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication, QMenu

from gaze_core.config import load_settings
from gaze_core.evaluation import evaluate
from gaze_core.profiles import profile_age_text
from overlay.actions import OverlayActions, TrayFallback
from overlay.panel import ControlPanel, summary_text
from overlay.hotkeys import Hotkeys
from overlay import platforms


class TrayFallbackTests(unittest.TestCase):
    def test_repeated_height_zero_notifies_only_once(self):
        fallback=TrayFallback();notice=Mock()
        for _ in range(20):
            if fallback.check(True,True,0,final=True):notice()
        notice.assert_called_once();self.assertTrue(fallback.notified)

    def test_available_but_not_laid_out_waits_until_final_check(self):
        fallback=TrayFallback()
        self.assertFalse(fallback.check(True,False,0))
        self.assertFalse(fallback.notified)
        self.assertFalse(fallback.check(True,True,0))
        self.assertTrue(fallback.check(True,True,0,final=True))

    def test_initial_zero_can_recover_before_final_without_fallback(self):
        fallback=TrayFallback()
        self.assertFalse(fallback.check(True,False,0))
        self.assertFalse(fallback.check(True,True,22,final=True))
        self.assertFalse(fallback.notified)

    def test_unavailable_falls_back_immediately_and_invisible_at_final_does_too(self):
        fallback=TrayFallback()
        self.assertTrue(fallback.check(False,False,0))
        self.assertFalse(fallback.check(False,False,0,final=True))
        fallback=TrayFallback()
        self.assertTrue(fallback.check(True,False,22,final=True))
        self.assertFalse(fallback.check(True,False,22,final=True))


class ControlPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.actions=OverlayActions()
        self.panel=ControlPanel(self.actions)
        self.menu=QMenu(self.panel);self.actions.add_to_menu(self.menu)

    def tearDown(self):
        self.panel.hide();self.panel.deleteLater();self.app.processEvents()

    def test_menu_and_panel_share_action_identity_text_and_enabled_state(self):
        self.assertEqual(set(self.panel.buttons),set(self.actions.items)-{'panel'})
        for name,button in self.panel.buttons.items():
            action=self.actions[name]
            self.assertIs(button.defaultAction(),action)
            self.assertIn(action,self.menu.actions())
        pause=self.actions['pause'];button=self.panel.buttons['pause']
        callback=Mock();self.actions.connect('pause',callback)
        pause.setText('恢复摄像头');pause.setEnabled(False)
        self.app.processEvents()
        self.assertEqual(button.text(),'恢复摄像头');self.assertFalse(button.isEnabled())
        self.assertEqual(next(a for a in self.menu.actions() if a is pause).text(),'恢复摄像头')
        button.click();callback.assert_not_called()
        pause.setEnabled(True);self.app.processEvents();button.click()
        callback.assert_called_once()
        pause.trigger();self.assertEqual(callback.call_count,2)

    def test_record_checked_state_is_shared_and_each_activation_calls_once(self):
        action=self.actions['record'];button=self.panel.buttons['record']
        callback=Mock();self.actions.connect('record',lambda checked:callback(checked))
        self.assertTrue(action.isCheckable());self.assertFalse(action.isChecked())
        button.click()
        self.assertTrue(action.isChecked());self.assertTrue(button.isChecked());callback.assert_called_once_with(True)
        action.trigger()
        self.assertFalse(action.isChecked());self.assertFalse(button.isChecked());self.assertEqual(callback.call_count,2)
        action.setChecked(True)
        self.assertTrue(button.isChecked());self.assertEqual(callback.call_count,2)

    def test_close_only_hides_and_panel_can_reopen_without_triggering_quit(self):
        quit_callback=Mock();self.actions.connect('quit',quit_callback)
        self.panel.show();self.app.processEvents();self.assertTrue(self.panel.isVisible())
        self.panel.close();self.app.processEvents()
        self.assertFalse(self.panel.isVisible());quit_callback.assert_not_called()
        self.panel.present();self.app.processEvents();self.assertTrue(self.panel.isVisible())
        self.panel.buttons['quit'].click();quit_callback.assert_called_once()

    def test_panel_hotkey_fronts_hidden_and_already_visible_accessory_window(self):
        self.actions.connect('panel',self.panel.present)
        keys=Hotkeys(dict(panel='Ctrl+Option+O'),lambda action:self.actions[action].trigger(),
                     backend_factory=lambda cb:Mock())
        try:
            for visible in (False,True):
                with self.subTest(visible=visible):
                    self.panel.setVisible(visible);self.app.processEvents()
                    calls=Mock()
                    application=Mock();native=Mock()
                    native.collectionBehavior.return_value=1|16|32|64
                    calls.attach_mock(application.activateIgnoringOtherApps_,'activate')
                    calls.attach_mock(native.makeKeyAndOrderFront_,'make_key')
                    calls.attach_mock(native.orderFrontRegardless,'order_front')
                    objc=SimpleNamespace(objc_object=Mock(return_value=SimpleNamespace(window=lambda:native)))
                    appkit=SimpleNamespace(NSApplication=SimpleNamespace(sharedApplication=lambda:application),
                        NSWindowCollectionBehaviorCanJoinAllSpaces=1,
                        NSWindowCollectionBehaviorMoveToActiveSpace=2,
                        NSWindowCollectionBehaviorFullScreenAuxiliary=8,
                        NSWindowCollectionBehaviorFullScreenPrimary=16,
                        NSWindowCollectionBehaviorFullScreenNone=32)
                    with patch.object(platforms.sys,'platform','darwin'), \
                         patch('PySide6.QtGui.QGuiApplication.platformName',return_value='cocoa'), \
                         patch.dict('sys.modules',objc=objc,AppKit=appkit),redirect_stdout(io.StringIO()):
                        keys._activated(1)
                    self.assertTrue(self.panel.isVisible())
                    self.assertEqual(calls.mock_calls,[unittest.mock.call.activate(True),
                        unittest.mock.call.make_key(None),unittest.mock.call.order_front()])
                    native.setCollectionBehavior_.assert_called_once_with(2|8|64)
                    objc.objc_object.assert_called_once_with(c_void_p=int(self.panel.winId()))
                    application.setActivationPolicy_.assert_not_called()
        finally:keys.close()

    def test_offscreen_panel_presentation_never_imports_or_activates_appkit(self):
        appkit=Mock()
        with patch.object(platforms.sys,'platform','darwin'), \
             patch('PySide6.QtGui.QGuiApplication.platformName',return_value='offscreen'), \
             patch.dict('sys.modules',AppKit=appkit):
            self.panel.present()
        self.assertTrue(self.panel.isVisible())
        appkit.NSApplication.sharedApplication.assert_not_called()

    def test_panel_shows_old_profile_age_without_expiring_or_changing_mode(self):
        now=datetime(2026,10,20,tzinfo=timezone.utc)
        for ago in (timedelta(minutes=12),timedelta(days=365)):
            profile=dict(created_at=(now-ago).isoformat(),pipeline='python')
            before=deepcopy(profile)
            mode='九点 · '+profile_age_text(profile,now)
            self.panel.mode.setText(mode);self.panel.show();self.app.processEvents()
            self.assertEqual(self.panel.mode.text(),mode);self.assertIn('九点',self.panel.mode.text())
            self.assertEqual(profile,before)
        self.assertIn('天前',self.panel.mode.text())


class SummaryPresentationTests(unittest.TestCase):
    def setUp(self):
        self.geometry=replace(load_settings(use_local=False).geometry,width_cm=100.,height_cm=100.,top_cm=-50.,measured=True)
        self.targets=[dict(target_id='center',region='center',target=[.5,.5]),
                      dict(target_id='edge-1',region='edge',target=[.95,.05]),
                      dict(target_id='edge-2',region='edge',target=[.05,.95])]
        self.frames=[]
        for target in self.targets:
            self.frames.extend([dict(target_id=target['target_id'],valid=True,
                                     raw=[target['target'][0]+.01,target['target'][1]+.02],origin_m=[0,0,.5])]
                               *(3 if target['region']=='center' else 1))
        self.frames[-1]['origin_m']=None
        self.matrix=[[1.,0.],[0.,1.],[-.01,-.02]]

    def test_summary_displays_evaluation_groups_regions_and_metrics_without_recalculating(self):
        summary=evaluate(self.frames,self.targets,self.geometry,self.matrix)
        before=deepcopy(summary);text=summary_text(summary,distance=48.)
        self.assertIn('逐目标中位后等权汇总（未滤波、未裁剪）',text)
        self.assertIn('距离中位数：48.000 cm（仅记录）',text)
        for group,values in summary['groups'].items():
            for region,title in (('all','全部'),('center','中央'),('edge','边缘')):
                block=re.search(rf'^{group} · {title}：.*?(?=^[AB] · |\Z)',text,re.MULTILINE|re.DOTALL).group()
                metrics=values[region]
                self.assertIn(f'{metrics["valid_targets"]}/{metrics["expected_targets"]} 点有厘米结果',block)
                for key,prefix,unit in (('mean_cm','平均 ',' cm'),('p90_cm','P90 ',' cm'),
                                        ('horizontal_cm','水平 ',' cm'),('vertical_cm','垂直 ',' cm'),
                                        ('horizontal_scale','水平缩放 ',''),('vertical_scale','垂直缩放 ',''),
                                        ('angle_deg','射线夹角 ','°'),('yaw_deg','yaw ','°'),('pitch_deg','pitch ','°')):
                    if metrics[key] is not None:self.assertIn(prefix+f'{metrics[key]:.3f}'+unit,block)
                for key,title in (('within15','≤1.5 cm '),('within2','≤2 cm ')):
                    self.assertIn(title+f'{metrics[key]*100:.3f}%',block)
                self.assertIn(f'角度有效帧 {metrics["angle_frame_count"]}；缺 origin {metrics["missing_origin_frames"]}',block)
                self.assertIn('右/下为正',block)
        self.assertEqual(summary,before)

    def test_distance_only_changes_recorded_display_line_and_not_statistics(self):
        summary=evaluate(self.frames,self.targets,self.geometry,self.matrix);before=deepcopy(summary)
        near=summary_text(summary,40.).splitlines();far=summary_text(summary,60.).splitlines()
        self.assertEqual(near[:1]+near[2:],far[:1]+far[2:])
        self.assertNotEqual(near[1],far[1]);self.assertIn('仅记录',near[1]);self.assertIn('仅记录',far[1])
        self.assertEqual(summary,before)

    def test_a_only_and_unmeasured_data_show_unavailable_without_invented_results(self):
        summary=evaluate(self.frames,self.targets,replace(self.geometry,measured=False))
        text=summary_text(summary)
        self.assertIn('A · 全部',text);self.assertNotIn('B ·',text)
        self.assertIn('距离中位数：不可用（仅记录）',text)
        self.assertIn('平均 不可用；P90 不可用',text)
        self.assertIn('射线夹角 不可用；yaw 不可用；pitch 不可用',text)


if __name__=='__main__':unittest.main()
