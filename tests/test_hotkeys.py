"""Native calls are fake: tests neither register real shortcuts nor observe keys."""
import ctypes as C
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from overlay.hotkeys import Hotkeys,parse_shortcut
from overlay.actions import panel_at_start,panel_on_tray_failure
from overlay.hotkeys_macos import MacHotkeys,HotKeyID,SIGNATURE,KEY_CODES,fourcc


class HotkeyTests(unittest.TestCase):
    def test_panel_policy_ignores_unreliable_mac_geometry(self):
        for platform in ('darwin','win32'):
            self.assertTrue(panel_at_start('always',platform))
            self.assertFalse(panel_at_start('never',platform))
            self.assertTrue(panel_at_start('never',platform,forced=True))
        self.assertTrue(panel_at_start('auto','darwin'))
        self.assertFalse(panel_at_start('auto','win32'))
        self.assertFalse(panel_on_tray_failure('never'))
        self.assertTrue(panel_on_tray_failure('auto'))

    def test_partial_conflict_invalid_and_disabled_with_dispatch_cleanup(self):
        backend=Mock();dispatch=Mock()
        backend.register.side_effect=[None,RuntimeError('conflict'),None]
        keys=Hotkeys(dict(toggle='Ctrl+Alt+G',pause='Ctrl+Alt+P',calibrate='Ctrl+Alt+C',panel=''),dispatch,
                     backend_factory=lambda cb:backend)
        keys._activated(1);keys._activated(2);keys._activated(3)
        self.assertEqual(dispatch.call_args_list,[unittest.mock.call('toggle'),unittest.mock.call('calibrate')])
        self.assertIn('失败',keys.status['pause']);self.assertEqual(keys.status['panel'],'已禁用')
        keys.close();keys.close();backend.close.assert_called_once()
        keys._activated(1);self.assertEqual(dispatch.call_count,2)
        keys=Hotkeys(dict(toggle='Ctrl+G',pause='Control+G',panel='nonsense'),dispatch,backend_factory=lambda cb:Mock())
        self.assertIn('重复',keys.status['pause']);self.assertIn('失败',keys.status['panel'])
        keys.close()

    def test_carbon_exact_event_codes_parameters_and_disposal(self):
        lib=SimpleNamespace(**{name:Mock(return_value=0) for name in
                              ('GetApplicationEventTarget','InstallEventHandler','RegisterEventHotKey','GetEventParameter','UnregisterEventHotKey','RemoveEventHandler')})
        def install(target,callback,count,event,user,ref):
            self.assertEqual((event._obj.eventClass,event._obj.eventKind),(fourcc('keyb'),5))
            ref._obj.value=99;return 0
        lib.InstallEventHandler.side_effect=install
        def register(key,mods,ident,target,opts,ref):
            self.assertEqual((key,mods,ident.signature,ident.id),(8,6144,SIGNATURE,1))
            ref._obj.value=123;return 0
        lib.RegisterEventHotKey.side_effect=register
        dispatch=Mock();keys=MacHotkeys(dispatch,library=lib)
        keys.register(1,*parse_shortcut('Ctrl+Option+C'))
        def parameter(event,name,kind,actual,size,used,out):
            self.assertEqual((name,kind,size),(fourcc('----'),fourcc('hkid'),C.sizeof(HotKeyID)))
            out._obj.signature=SIGNATURE;out._obj.id=1;return 0
        lib.GetEventParameter.side_effect=parameter
        self.assertEqual(keys._event(None,None,None),0);dispatch.assert_called_once_with(1)
        keys.close();keys.close()
        lib.UnregisterEventHotKey.assert_called_once();lib.RemoveEventHandler.assert_called_once()
        self.assertEqual(KEY_CODES['G'],5);self.assertEqual(KEY_CODES['O'],31)

    def test_windows_registers_only_hotkeys_and_filters_only_registered_messages(self):
        from overlay.hotkeys_windows import WindowsHotkeys
        lib=SimpleNamespace(RegisterHotKey=Mock(return_value=1),UnregisterHotKey=Mock(return_value=1))
        app=Mock();dispatch=Mock();keys=WindowsHotkeys(dispatch,library=lib,app=app)
        keys.register(1,*parse_shortcut('Ctrl+Alt+G'))
        lib.RegisterHotKey.assert_called_once_with(None,0x7001,0x4003,ord('G'))
        self.assertFalse(keys.message(0x100,0x7001))
        self.assertFalse(keys.message(0x312,0x7002))
        self.assertTrue(keys.message(0x312,0x7001));dispatch.assert_called_once_with(1)
        keys.close();lib.UnregisterHotKey.assert_called_once_with(None,0x7001)
        app.removeNativeEventFilter.assert_called_once_with(keys)


if __name__=='__main__':unittest.main()
