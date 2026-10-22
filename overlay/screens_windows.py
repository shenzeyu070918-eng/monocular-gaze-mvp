"""WMI connection type + GDI device interface identity; no primary-screen guess."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import replace
from functools import lru_cache
import json
import re
import subprocess
from .screens import BuiltinScreen,logical_rect

BUILTIN_TECHNOLOGIES={6,11,13,0x80000000,-2147483648}


def monitor_key(value):
    value=value.upper().removeprefix('\\\\?\\').replace('#','\\')
    parts=value.split('\\')
    if len(parts)<3:return None
    return '\\'.join((parts[0],parts[1],re.sub(r'_\d+$','',parts[2])))


@lru_cache(maxsize=1)
def connections():
    # Laptop panel connection technology/identity is stable within this
    # process. Hotplug checks use live GDI active interfaces, not PowerShell
    # on every GUI topology callback. Never cache the active QScreen choice.
    command=('Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams | '
             'Where-Object Active | Select-Object InstanceName,VideoOutputTechnology | ConvertTo-Json -Compress')
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
                              capture_output=True,text=True,timeout=8,check=True,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=json.loads(result.stdout)
        return data if isinstance(data,list) else [data]
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        raise RuntimeError('无法查询内置屏连接身份，请检查Windows显示设备驱动。') from exc


class DisplayDevice(C.Structure):
    _fields_=[('cb',W.DWORD),('DeviceName',W.WCHAR*32),('DeviceString',W.WCHAR*128),
              ('StateFlags',W.DWORD),('DeviceID',W.WCHAR*128),('DeviceKey',W.WCHAR*128)]


def device_interfaces(user=None):
    user=user or C.WinDLL('user32',use_last_error=True)
    enum=user.EnumDisplayDevicesW
    enum.argtypes=[W.LPCWSTR,W.DWORD,C.POINTER(DisplayDevice),W.DWORD];enum.restype=W.BOOL
    rows=[];index=0
    while True:
        adapter=DisplayDevice();adapter.cb=C.sizeof(adapter)
        if not enum(None,index,C.byref(adapter),0):break
        index+=1
        if not adapter.StateFlags&1:continue
        child=0
        while True:
            monitor=DisplayDevice();monitor.cb=C.sizeof(monitor)
            if not enum(adapter.DeviceName,child,C.byref(monitor),1):break  # EDD_GET_DEVICE_INTERFACE_NAME
            child+=1
            if monitor.StateFlags&1:rows.append((adapter.DeviceName,monitor.DeviceID))
    return rows


def select(screens,*,rows=None,interfaces=None):
    rows=connections() if rows is None else rows
    builtins=[r for r in rows if isinstance(r,dict) and r.get('VideoOutputTechnology') in BUILTIN_TECHNOLOGIES]
    keys={monitor_key(r.get('InstanceName','')) for r in builtins}
    if len(keys)!=1 or None in keys:raise RuntimeError('内置屏不可用或无法唯一识别；可能已合盖，不自动改用外接主屏。')
    interfaces=device_interfaces() if interfaces is None else interfaces
    names={name for name,ident in interfaces if monitor_key(ident) in keys}
    candidates=[s for s in screens if s.name() in names]
    if len(candidates)!=1:raise RuntimeError('无法将内置屏设备身份唯一对应到Qt逻辑屏幕。')
    screen=candidates[0];rect=replace(logical_rect(screen),native_name=screen.name())
    return BuiltinScreen(screen,rect,next(iter(keys)))
