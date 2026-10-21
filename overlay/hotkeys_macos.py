"""Carbon application-target hotkeys. No event tap or Input Monitoring."""
import ctypes as C


def fourcc(value):return int.from_bytes(value.encode('ascii'),'big')


class HotKeyID(C.Structure):
    _fields_=[('signature',C.c_uint32),('id',C.c_uint32)]


class EventType(C.Structure):
    _fields_=[('eventClass',C.c_uint32),('eventKind',C.c_uint32)]


# Physical ANSI key codes from HIToolbox Events.h; labels refer to these keys.
KEY_CODES=dict(A=0,S=1,D=2,F=3,H=4,G=5,Z=6,X=7,C=8,V=9,B=11,
               Q=12,W=13,E=14,R=15,Y=16,T=17,O=31,U=32,I=34,P=35,
               L=37,J=38,K=40,N=45,M=46)
KEY_CODES.update(dict(zip('1234659780',(18,19,20,21,22,23,25,26,28,29))))
MODIFIERS={'CTRL':4096,'ALT':2048,'SHIFT':512,'META':256}
SIGNATURE=fourcc('Gaze')
HANDLER=C.CFUNCTYPE(C.c_int32,C.c_void_p,C.c_void_p,C.c_void_p)


class MacHotkeys:
    def __init__(self,dispatch,*,library=None):
        self.library=library or C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        self.refs={};self.handler_ref=C.c_void_p();self.dispatch=dispatch
        lib=self.library
        specs={
            'GetApplicationEventTarget':([],C.c_void_p),
            'InstallEventHandler':([C.c_void_p,HANDLER,C.c_uint32,C.POINTER(EventType),C.c_void_p,C.POINTER(C.c_void_p)],C.c_int32),
            'RegisterEventHotKey':([C.c_uint32,C.c_uint32,HotKeyID,C.c_void_p,C.c_uint32,C.POINTER(C.c_void_p)],C.c_int32),
            'GetEventParameter':([C.c_void_p,C.c_uint32,C.c_uint32,C.c_void_p,C.c_uint32,C.c_void_p,C.c_void_p],C.c_int32),
            'UnregisterEventHotKey':([C.c_void_p],C.c_int32),
            'RemoveEventHandler':([C.c_void_p],C.c_int32)}
        for name,(args,result) in specs.items():
            function=getattr(lib,name);function.argtypes=args;function.restype=result
        self.target=lib.GetApplicationEventTarget()
        self.callback=HANDLER(self._event)  # Retain throughout native registration.
        event=EventType(fourcc('keyb'),5)  # kEventHotKeyPressed, not general key presses.
        status=lib.InstallEventHandler(self.target,self.callback,1,C.byref(event),None,C.byref(self.handler_ref))
        if status:raise RuntimeError(f'Carbon InstallEventHandler OSStatus={status}')

    def _event(self,_,event,__):
        ident=HotKeyID()
        status=self.library.GetEventParameter(event,fourcc('----'),fourcc('hkid'),None,C.sizeof(ident),None,C.byref(ident))
        if status or ident.signature!=SIGNATURE or ident.id not in self.refs:return -9874
        # Qt action dispatch is queued by the runtime; never let exceptions cross C.
        try:self.dispatch(ident.id)
        except Exception as exc:print('系统热键分发失败：'+str(exc),flush=True)
        return 0

    def register(self,ident,modifiers,key):
        ref=C.c_void_p()
        status=self.library.RegisterEventHotKey(KEY_CODES[key],sum(MODIFIERS[v] for v in modifiers),HotKeyID(SIGNATURE,ident),self.target,0,C.byref(ref))
        if status:raise RuntimeError(f'Carbon RegisterEventHotKey OSStatus={status}（可能与输入法或其他应用冲突）')
        self.refs[ident]=ref

    def close(self):
        for ref in self.refs.values():self.library.UnregisterEventHotKey(ref)
        self.refs.clear()
        if self.handler_ref:
            self.library.RemoveEventHandler(self.handler_ref);self.handler_ref=C.c_void_p()
