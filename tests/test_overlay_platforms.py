from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from overlay import platforms


class NativeAdapterTests(unittest.TestCase):
    def test_multiscreen_rejected_before_selecting_external_as_primary(self):
        with self.assertRaisesRegex(RuntimeError,'外接'):platforms.builtin_screen([object(),object()])

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
        with patch.object(platforms.sys,'platform','win32'),patch.object(platforms.subprocess,'run') as run:
            screen=object()
            for value in ('6','11','13','2147483648'):
                run.return_value=SimpleNamespace(stdout=value)
                self.assertIs(platforms.builtin_screen([screen]),screen)
            for value in ('5','null','[6,5]','invalid'):
                run.return_value=SimpleNamespace(stdout=value)
                with self.assertRaisesRegex(RuntimeError,'内置屏'):platforms.builtin_screen([screen])


if __name__=='__main__':unittest.main()
