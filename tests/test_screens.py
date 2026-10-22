"""Native identities and topology signals use fakes, no projector or camera."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from PySide6.QtCore import QRect,QObject,Signal
from PySide6.QtWidgets import QApplication
from overlay.mapping import ScreenRect
from overlay.screens import BuiltinScreen,ScreenMonitor,transition
from overlay.screens_macos import select as select_mac
from overlay.screens_windows import select as select_windows,monitor_key


class Screen(QObject):
    geometryChanged=Signal(object);logicalDotsPerInchChanged=Signal(float);physicalDotsPerInchChanged=Signal(float)
    def __init__(self,name,rect,dpr=1.):super().__init__();self.label=name;self.rect=QRect(*rect);self.dpr=dpr
    def geometry(self):return self.rect
    def devicePixelRatio(self):return self.dpr
    def name(self):return self.label


def native(ident,name,rect,dpr):
    x,y,w,h=rect
    return NS(deviceDescription=lambda:dict(NSScreenNumber=ident),localizedName=lambda:name,
              frame=lambda:NS(origin=NS(x=x,y=y),size=NS(width=w,height=h)),backingScaleFactor=lambda:dpr)


def mac_setup(*,mirrored=False,closed=False,external_primary=False):
    internal=Screen('Internal',(-1200,0,1200,800),2)
    external=Screen('Projector',(0,0,1600,1000),1)
    ns=[native(2,'Projector',(0,0,1600,1000),1)]
    if not mirrored:ns+=[native(1,'Internal',(-1200,200,1200,800),2)]
    quartz=NS(CGGetOnlineDisplayList=lambda *args:(0,(2,) if closed else (1,2),1 if closed else 2),
              CGDisplayIsBuiltin=lambda i:i==1,CGDisplayIsAsleep=lambda i:False,
              CGDisplayMirrorsDisplay=lambda i:2 if mirrored and i==1 else 0,
              CGMainDisplayID=lambda:2,CGDisplayBounds=lambda _:NS(size=NS(height=1000)),
              CGDisplayVendorNumber=lambda i:1,CGDisplayModelNumber=lambda i:2,CGDisplaySerialNumber=lambda i:3)
    return [external,internal],quartz,NS(NSScreen=NS(screens=lambda:ns))


class ScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_mac_extended_external_primary_resolves_builtin_not_list_order(self):
        qt,q,a=mac_setup();selected=select_mac(qt,quartz=q,appkit=a)
        self.assertIs(selected.qscreen,qt[1]);self.assertEqual(selected.identity,'builtin:1:2:3')
        self.assertEqual(selected.rect,ScreenRect(-1200,0,1200,800,2,cocoa_top=1000))
        self.assertEqual(selected.rect.global_position((1,1)),(-1,799))
        self.assertEqual(selected.rect.cocoa_top,1000)

    def test_mac_hardware_mirror_builtin_slave_has_only_source_QScreen(self):
        qt,q,a=mac_setup(mirrored=True)
        selected=select_mac(qt[:1],quartz=q,appkit=a)
        self.assertIs(selected.qscreen,qt[0]);self.assertEqual(selected.identity,'builtin:1:2:3')
        self.assertEqual(selected.rect.width,1600);self.assertEqual(selected.rect.dpr,1)
        # If the external is mirroring the built-in, resolve in the other direction.
        qt,q,a=mac_setup();q.CGDisplayMirrorsDisplay=lambda i:1 if i==2 else 0
        self.assertIs(select_mac(qt,quartz=q,appkit=a).qscreen,qt[1])

    def test_mac_closed_sleeping_or_ambiguous_mapping_fails_closed(self):
        qt,q,a=mac_setup(closed=True)
        with self.assertRaisesRegex(RuntimeError,'内置屏不可用'):select_mac(qt[:1],quartz=q,appkit=a)
        qt,q,a=mac_setup();q.CGDisplayIsAsleep=lambda _:True
        with self.assertRaisesRegex(RuntimeError,'内置屏不可用'):select_mac(qt,quartz=q,appkit=a)
        qt,q,a=mac_setup()
        with self.assertRaisesRegex(RuntimeError,'唯一对应'):select_mac(qt+[qt[1]],quartz=q,appkit=a)
        q.CGDisplayMirrorsDisplay=lambda i:3-i
        with self.assertRaisesRegex(RuntimeError,'尚未稳定'):select_mac(qt,quartz=q,appkit=a)

    def test_mac_internal_alone_supported(self):
        qt,q,a=mac_setup();q.CGGetOnlineDisplayList=lambda *args:(0,(1,),1)
        self.assertIs(select_mac(qt[1:],quartz=q,appkit=a).qscreen,qt[1])

    def test_windows_wmi_identity_maps_specific_gdi_screen_and_mirror(self):
        qt=[Screen(r'\\.\DISPLAY2',(1200,0,1200,800)),Screen(r'\\.\DISPLAY1',(0,0,1200,800),1.5)]
        rows=[dict(InstanceName=r'DISPLAY\PANEL\5&ABC_0',VideoOutputTechnology=11),
              dict(InstanceName=r'DISPLAY\PROJECTOR\6&DEF_0',VideoOutputTechnology=5)]
        interfaces=[(r'\\.\DISPLAY2',r'\\?\DISPLAY#PROJECTOR#6&DEF#{guid}'),
                    (r'\\.\DISPLAY1',r'\\?\DISPLAY#PANEL#5&ABC#{guid}')]
        selected=select_windows(qt,rows=rows,interfaces=interfaces)
        self.assertIs(selected.qscreen,qt[1]);self.assertEqual(selected.rect.native_name,r'\\.\DISPLAY1')
        self.assertEqual(monitor_key(rows[0]['InstanceName']),monitor_key(interfaces[1][1]))
        self.assertIs(select_windows(qt[1:],rows=rows,interfaces=interfaces).qscreen,qt[1])
        with self.assertRaisesRegex(RuntimeError,'不可用'):select_windows(qt[:1],rows=rows[1:],interfaces=interfaces)
        with self.assertRaisesRegex(RuntimeError,'唯一对应'):select_windows(qt[:1],rows=rows,interfaces=interfaces)

    def test_topology_comparison_ignores_external_count_but_not_size_dpr_or_identity(self):
        first=BuiltinScreen(object(),ScreenRect(0,0,1200,800,2),'same')
        self.assertEqual(transition(first,replace(first,qscreen=object())),'unchanged')
        self.assertEqual(transition(first,replace(first,rect=replace(first.rect,cocoa_top=1200,native_name='mirror-source'))),'unchanged')
        for current in (replace(first,rect=ScreenRect(0,0,1000,700,2)),replace(first,rect=ScreenRect(0,0,1200,800,1)),replace(first,identity='other')):
            self.assertEqual(transition(first,current),'changed')
        self.assertEqual(transition(first,None),'unavailable')

    def test_monitor_coalesces_signals_rewires_new_screen_and_disconnects(self):
        class App(QObject):
            screenAdded=Signal(object);screenRemoved=Signal(object);primaryScreenChanged=Signal(object)
            def screens(self):return list(self.items)
        app=App();s=Screen('Internal',(0,0,1200,800),2);app.items=[s]
        selected=BuiltinScreen(s,ScreenRect(0,0,1200,800,2),'same')
        selector=Mock(return_value=selected);changed=Mock()
        monitor=ScreenMonitor(app,selected,changed,selector=selector,delay_ms=300)
        app.screenAdded.emit(object());s.geometryChanged.emit(QRect(0,0,1200,800));app.primaryScreenChanged.emit(s)
        selector.assert_not_called();self.assertTrue(monitor.timer.isActive())
        monitor.timer.stop();monitor.refresh();selector.assert_called_once();changed.assert_called_once_with('unchanged',selected,None)
        e=Screen('External',(-1000,0,1000,700));app.items.append(e);monitor.refresh()
        e.geometryChanged.emit(e.geometry());self.assertTrue(monitor.timer.isActive())
        monitor.close();e.geometryChanged.emit(e.geometry());self.assertFalse(monitor.timer.isActive())

    def test_transient_native_failure_retries_without_pausing_and_detach_hides_immediately(self):
        class App(QObject):
            screenAdded=Signal(object);screenRemoved=Signal(object)
            def screens(self):return []
        app=App();s=object();initial=BuiltinScreen(s,ScreenRect(0,0,1200,800),'same')
        selector=Mock(side_effect=[RuntimeError('updating'),initial]);changed=Mock();hidden=Mock()
        monitor=ScreenMonitor(app,initial,changed,selector=selector,retry_count=1,on_detach=hidden)
        app.screenRemoved.emit(s);hidden.assert_called_once();changed.assert_not_called()
        monitor.timer.stop();monitor.refresh();changed.assert_not_called();self.assertTrue(monitor.timer.isActive())
        monitor.timer.stop();monitor.refresh();changed.assert_called_once_with('unchanged',initial,None)
        selector.side_effect=RuntimeError('closed')
        monitor.refresh();monitor.timer.stop();monitor.refresh()
        self.assertEqual(changed.call_args.args[0],'unavailable');monitor.close()

    def test_startup_retries_qt_native_disagreement_but_never_substitutes_external(self):
        from overlay.screens import startup_screen
        app=Mock();initial=BuiltinScreen(object(),ScreenRect(0,0,1200,800),'same')
        selector=Mock(side_effect=[RuntimeError('initializing'),initial]);wait=Mock()
        self.assertIs(startup_screen(app,selector=selector,wait=wait),initial)
        self.assertEqual(app.processEvents.call_count,2);wait.assert_called_once()
        selector=Mock(side_effect=RuntimeError('内置屏不可用'));wait.reset_mock()
        with self.assertRaisesRegex(RuntimeError,'内置屏不可用'):startup_screen(app,selector=selector,wait=wait,retry_count=2)
        self.assertEqual(selector.call_count,3);self.assertEqual(wait.call_count,2)

    def test_windows_caches_connection_identity_but_selection_uses_live_interfaces(self):
        from overlay.screens_windows import connections
        from unittest.mock import patch
        connections.cache_clear()
        try:
            with patch('overlay.screens_windows.subprocess.run',return_value=NS(stdout='{"InstanceName":"DISPLAY\\\\PANEL\\\\INSTANCE_0","VideoOutputTechnology":11}')) as run:
                self.assertEqual(connections(),connections());run.assert_called_once()
        finally:connections.cache_clear()


if __name__=='__main__':unittest.main()
