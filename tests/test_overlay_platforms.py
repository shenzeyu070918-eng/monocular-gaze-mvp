from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from overlay import platforms


class NativeAdapterTests(unittest.TestCase):
    def test_multiscreen_selects_verified_native_screen_instead_of_first_primary(self):
        from overlay.screens import BuiltinScreen
        from overlay.mapping import ScreenRect
        first,internal=object(),object()
        with patch('overlay.screens.select_builtin',return_value=BuiltinScreen(internal,ScreenRect(0,0,1200,800),'builtin')):
            self.assertIs(platforms.builtin_screen([first,internal]),internal)

    def test_mac_native_flags_and_no_mouse_input(self):
        native=Mock();native.collectionBehavior.return_value=24
        view=Mock();view.window.return_value=native
        objc=SimpleNamespace(objc_object=Mock(return_value=view))
        appkit=SimpleNamespace(NSWindowCollectionBehaviorCanJoinAllSpaces=1,
                              NSWindowCollectionBehaviorFullScreenAuxiliary=2,
                              NSWindowCollectionBehaviorCanJoinAllApplications=4,NSStatusWindowLevel=25,
                              NSWindowCollectionBehaviorMoveToActiveSpace=8,NSWindowCollectionBehaviorFullScreenPrimary=16)
        with patch.object(platforms.sys,'platform','darwin'),patch.dict('sys.modules',objc=objc,AppKit=appkit):
            platforms.configure_window(SimpleNamespace(winId=lambda:123))
        objc.objc_object.assert_called_once_with(c_void_p=123)
        native.setCollectionBehavior_.assert_called_once_with(7)
        native.setIgnoresMouseEvents_.assert_called_once_with(True)
        native.setHasShadow_.assert_called_once_with(False)

    def test_invalidate_shadow_only_uses_configured_overlay_window_on_mac(self):
        native=Mock();widget=SimpleNamespace(_overlay_native_window=native)
        with patch.object(platforms.sys,'platform','darwin'):
            platforms.invalidate_shadow(widget)
            platforms.invalidate_shadow(SimpleNamespace())
        native.invalidateShadow.assert_called_once_with()
        with patch.object(platforms.sys,'platform','win32'):platforms.invalidate_shadow(widget)
        native.invalidateShadow.assert_called_once_with()

    def test_permission_preflight_denied_or_unknown_never_starts_capture(self):
        device=Mock();av=SimpleNamespace(AVCaptureDevice=device,AVMediaTypeVideo='video')
        with patch.object(platforms.sys,'platform','darwin'),patch.dict('sys.modules',AVFoundation=av):
            for status in (1,2):
                device.authorizationStatusForMediaType_.return_value=status
                with self.assertRaisesRegex(RuntimeError,'终端授权'):platforms.camera_permission()
            device.authorizationStatusForMediaType_.return_value=0
            self.assertIn('预热超时',platforms.camera_permission())
            device.authorizationStatusForMediaType_.return_value=3
            self.assertIsNone(platforms.camera_permission())
        device.requestAccessForMediaType_completionHandler_.assert_not_called()

    def test_windows_builtin_technology_and_unknown_fail_closed(self):
        from PySide6.QtCore import QRect
        screen=SimpleNamespace(name=lambda:'DISPLAY1',geometry=lambda:QRect(0,0,1200,800),devicePixelRatio=lambda:1.)
        with patch.object(platforms.sys,'platform','win32'),patch('overlay.screens_windows.connections') as rows,patch('overlay.screens_windows.device_interfaces',return_value=[('DISPLAY1',r'\\?\DISPLAY#PANEL#INSTANCE#{guid}')]):
            for value in (6,11,13,2147483648):
                rows.return_value=[dict(InstanceName=r'DISPLAY\PANEL\INSTANCE_0',VideoOutputTechnology=value)]
                self.assertIs(platforms.builtin_screen([screen]),screen)
            rows.return_value=[dict(InstanceName=r'DISPLAY\PANEL\INSTANCE_0',VideoOutputTechnology=5)]
            with self.assertRaisesRegex(RuntimeError,'内置屏'):platforms.builtin_screen([screen])


if __name__=='__main__':unittest.main()
