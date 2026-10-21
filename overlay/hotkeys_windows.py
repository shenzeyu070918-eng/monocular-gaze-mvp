"""RegisterHotKey and WM_HOTKEY only; no keyboard hooks."""
import ctypes as C
from ctypes import wintypes as W
from PySide6.QtCore import QAbstractNativeEventFilter,QCoreApplication

MODIFIERS={'ALT':1,'CTRL':2,'SHIFT':4,'META':8}


class WindowsHotkeys(QAbstractNativeEventFilter):
    def __init__(self,dispatch,*,library=None,app=None):
        super().__init__()
        self.library=library or C.WinDLL('user32',use_last_error=True)
        self.library.RegisterHotKey.argtypes=[W.HWND,C.c_int,W.UINT,W.UINT]
        self.library.RegisterHotKey.restype=W.BOOL
        self.library.UnregisterHotKey.argtypes=[W.HWND,C.c_int]
        self.library.UnregisterHotKey.restype=W.BOOL
        self.dispatch=dispatch;self.ids=set();self.app=app or QCoreApplication.instance()
        self.app.installNativeEventFilter(self)

    def register(self,ident,modifiers,key):
        # Thread-scoped messages; IDs allocated outside Qt's application range.
        native_id=0x7000+ident
        if not self.library.RegisterHotKey(None,native_id,0x4000|sum(MODIFIERS[v] for v in modifiers),ord(key)):
            error=C.get_last_error()
            raise RuntimeError(f'RegisterHotKey WinError={error}（可能与其他应用冲突）')
        self.ids.add(native_id)

    def message(self,message,ident):
        if message!=0x0312 or ident not in self.ids:return False
        self.dispatch(ident-0x7000);return True

    def nativeEventFilter(self,event_type,message):
        if bytes(event_type) not in (b'windows_generic_MSG',b'windows_dispatcher_MSG'):return False,0
        msg=W.MSG.from_address(int(message))
        return self.message(msg.message,int(msg.wParam)),0

    def close(self):
        for ident in self.ids:self.library.UnregisterHotKey(None,ident)
        self.ids.clear();self.app.removeNativeEventFilter(self)
