"""Heatmap worker + Qt rendering tests use synthetic grids and fake clocks."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from dataclasses import replace
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock,patch
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from gaze_core.config import load_settings,OverlaySettings
from gaze_core.capture import Prediction
from gaze_core.heatmap_render import render_heatmap
from overlay.heatmap import HeatmapWorker
from overlay.mapping import ScreenRect
from overlay.window import OverlayWindow
from overlay.hotkeys import Hotkeys
from overlay.actions import OverlayActions
from test_heatmap import OPTIONS,context


class HeatmapWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.now=0;self.exported=[];self.workers=[]
        self.settings=replace(load_settings(use_local=False),heatmap=OPTIONS)

    def worker(self,**kwargs):
        worker=HeatmapWorker(kwargs.pop('settings',self.settings),ScreenRect(0,0,1200,800),clock_ns=lambda:self.now,
                             exporter=kwargs.pop('exporter',lambda snap,**kw:self.exported.append((snap,threading.current_thread().name)) or 'synthetic'),render_log=Mock(path=None,last=None),**kwargs)
        self.workers.append(worker);return worker

    def fence(self,worker):
        done=threading.Event();self.assertTrue(worker._put(('barrier',done)));self.assertTrue(done.wait(3))

    def feed(self,worker,t,**updates):
        data=dict(raw=[.5,.5],origin_m=[0,0,.5],valid=True,status='tracking',matrix_revision=1,**updates)
        return worker.observe(Prediction(1,round(t*1e9),round(t*1e9),data))

    def tearDown(self):
        for worker in self.workers:worker.close();self.assertFalse(worker.thread.is_alive())

    def test_default_off_unmatched_blocked_forced_labelled_and_no_keyboard_hook(self):
        worker=self.worker();self.assertFalse(worker.state()['enabled']);self.assertFalse(worker.set_enabled(True))
        self.assertIn('未校准',worker.state()['error'])
        worker.configure(context());self.assertTrue(worker.set_enabled(True));self.fence(worker)
        self.assertTrue(worker.state()['enabled'])
        worker.configure(context(matrix=None));self.fence(worker);self.assertFalse(worker.state()['enabled'])
        forced=self.worker(settings=replace(self.settings,heatmap=replace(OPTIONS,allow_uncalibrated=True)))
        forced.configure(context(matrix=None));self.assertTrue(forced.set_enabled(True));self.fence(forced)
        self.assertIn('未校准',forced.state()['label'])
        actions=OverlayActions();dispatch=Mock()
        keys=Hotkeys(dict(heatmap_toggle='Ctrl+Alt+H',heatmap_view='Ctrl+Alt+V'),dispatch,backend_factory=lambda cb:Mock())
        keys._activated(1);keys._activated(2)
        self.assertEqual([c.args[0] for c in dispatch.call_args_list],['heatmap_toggle','heatmap_view']);keys.close()
        self.assertTrue(actions['heatmap_toggle'].isCheckable())
        self.assertTrue(actions['heatmap_view'].isCheckable())

    def test_export_is_same_accumulator_not_replay_and_runs_on_worker(self):
        worker=self.worker();worker.configure(context());worker.set_enabled(True)
        for t in (0,.07,.14):self.feed(worker,t)
        self.now=150_000_000;self.fence(worker);self.assertIsNone(worker.state()['latest'])
        self.assertTrue(worker.export());self.fence(worker)
        snap,thread=self.exported[0];self.assertEqual(thread,'gaze-heatmap')
        self.assertAlmostEqual(snap['regions']['total_fixation_s'],.14)
        self.assertEqual(snap['source'],'live_accumulator');self.assertEqual(snap['unit'],'decay_weighted_seconds_per_cell')
        self.assertIsNone(worker.state()['latest']);self.assertTrue(worker.state()['has_session'])
        worker.clear();self.fence(worker);worker.export();self.fence(worker)
        self.assertEqual(self.exported[-1][0]['grid'].sum(),0.)

    def test_click_coarse_calibration_is_allowed_labelled_and_exported_as_C(self):
        value=deepcopy(context());value['phase']['group']='C';value['kernel']['source']='matching_C_check'
        worker=self.worker();worker.configure(value);self.assertTrue(worker.set_enabled(True))
        for t in (0,.07,.14):self.feed(worker,t)
        self.now=150_000_000;self.fence(worker)
        self.assertIn('粗校准',worker.state()['label']);self.assertFalse(worker.state()['uncalibrated'])
        self.assertIn('匹配 C',worker.state()['kernel_text'])
        worker.export();self.fence(worker)
        snapshot=self.exported[-1][0]
        self.assertTrue(snapshot['coarse_calibrated']);self.assertFalse(snapshot['uncalibrated'])
        self.assertAlmostEqual(snapshot['regions']['total_fixation_s'],.14)

    def test_no_new_data_same_clock_does_not_republish_and_pause_does_not_bridge(self):
        worker=self.worker();worker.configure(context());worker.set_enabled(True)
        for t in (0,.07,.14):self.feed(worker,t)
        self.now=150_000_000;worker.set_viewing(True);self.fence(worker);version=worker.state()['latest']['version']
        self.fence(worker);self.assertEqual(worker.state()['latest']['version'],version)
        worker.interrupt();self.fence(worker)
        for t in (4.,4.07,4.14):self.feed(worker,t)
        self.now=4_150_000_000;worker.export();self.fence(worker)
        self.assertAlmostEqual(self.exported[-1][0]['regions']['total_fixation_s'],.28)

    def test_full_queue_fails_heatmap_only_and_export_failure_is_visible(self):
        entered=threading.Event();resume=threading.Event()
        def exporter(*args,**kwargs):entered.set();self.assertTrue(resume.wait(3));raise OSError('synthetic disk failure')
        worker=self.worker(settings=replace(self.settings,heatmap=replace(OPTIONS,queue_size=2)),exporter=exporter)
        worker.configure(context());self.fence(worker);worker.set_enabled(True);self.fence(worker)
        worker.export();self.assertTrue(entered.wait(3))
        try:
            self.assertTrue(self.feed(worker,0));self.assertTrue(self.feed(worker,.07));self.assertTrue(self.feed(worker,.14))
            self.assertFalse(worker.state()['enabled']);self.assertIn('队列已满',worker.state()['error'])
        finally:resume.set()
        # Closing drains the admitted commands even after export fails.
        worker.close();self.assertIn('synthetic disk failure',worker.state()['error'])

    def test_shutdown_can_retry_while_export_finishes_and_does_not_reopen(self):
        entered=threading.Event();resume=threading.Event()
        def exporter(*args,**kwargs):entered.set();self.assertTrue(resume.wait(3));return 'synthetic'
        settings=replace(self.settings,recording=replace(self.settings.recording,close_timeout_s=.01))
        worker=self.worker(settings=settings,exporter=exporter)
        worker.configure(context());worker.set_enabled(True);self.fence(worker)
        worker.export();self.assertTrue(entered.wait(3))
        try:
            with self.assertRaisesRegex(RuntimeError,'尚未退出'):worker.close()
            self.assertFalse(worker.set_enabled(True))
        finally:resume.set()
        worker.thread.join(3);worker.close();self.assertFalse(worker.thread.is_alive())

    def test_forced_uncalibrated_contributions_keep_badge_after_matching_until_clear(self):
        worker=self.worker(settings=replace(self.settings,heatmap=replace(OPTIONS,allow_uncalibrated=True)))
        worker.configure(context(matrix=None));worker.set_enabled(True)
        for t in (0,.07,.14):self.feed(worker,t)
        self.now=150_000_000;self.fence(worker)
        worker.configure(context(2));self.fence(worker)
        self.assertTrue(worker.state()['uncalibrated'])
        worker.clear();self.fence(worker);self.assertFalse(worker.state()['uncalibrated'])

    def test_hidden_collection_does_not_snapshot_or_colorize_but_can_export(self):
        worker=self.worker();worker.configure(context());worker.set_enabled(True);self.fence(worker)
        with patch('overlay.heatmap.render_heatmap',wraps=render_heatmap) as colors,patch.object(worker._stream,'snapshot',wraps=worker._stream.snapshot) as snapshot:
            for t in (0,.07,.14):self.feed(worker,t)
            self.now=150_000_000;self.fence(worker)
            colors.assert_not_called();snapshot.assert_not_called()
            worker._render_log.record.assert_not_called()
            self.assertTrue(worker.enabled);self.assertFalse(worker.viewing)
            self.assertIsNone(worker.state()['latest'])
            self.assertIn('2.000 cm',worker.state()['kernel_text']);self.assertIn('最近匹配 B',worker.state()['kernel_text'])
            worker.export();self.fence(worker)
            snapshot.assert_called_once();colors.assert_not_called()
        self.assertAlmostEqual(self.exported[-1][0]['regions']['total_fixation_s'],.14)

    def test_both_modes_view_at_one_hz_and_hide_preserves_accumulation(self):
        for mode in ('on_demand','continuous'):
            with self.subTest(mode=mode):
                worker=self.worker(settings=replace(self.settings,heatmap=replace(OPTIONS,display_mode=mode)))
                worker.configure(context())
                with patch('overlay.heatmap.render_heatmap',wraps=render_heatmap) as colors:
                    self.now=0;worker.set_enabled(True);self.fence(worker)
                    self.assertEqual(worker.viewing,mode=='continuous')
                    self.assertEqual(colors.call_count,1 if mode=='continuous' else 0)
                    worker.set_viewing(True);self.fence(worker);self.assertEqual(colors.call_count,1)
                    self.now=500_000_000;self.fence(worker);self.assertEqual(colors.call_count,1)
                    self.now=1_000_000_000;self.fence(worker);self.assertEqual(colors.call_count,2)
                    worker.set_viewing(False);self.fence(worker)
                    for t in (1.,1.07,1.14):
                        self.now=round(t*1e9);self.feed(worker,t);self.fence(worker)
                    self.now=3_000_000_000;self.fence(worker);self.assertEqual(colors.call_count,2)
                    self.assertTrue(worker.enabled);self.assertFalse(worker.viewing)
                    worker.export();self.fence(worker)
                    self.assertAlmostEqual(self.exported[-1][0]['regions']['total_fixation_s'],.14)
                worker.close()

    def test_view_rejected_when_not_collecting_and_stop_always_hides(self):
        worker=self.worker();self.assertFalse(worker.set_viewing(True));self.assertFalse(worker.viewing)
        worker.configure(context());worker.set_enabled(True);worker.set_viewing(True);self.fence(worker)
        self.assertTrue(worker.viewing);worker.set_enabled(False);self.fence(worker)
        self.assertFalse(worker.viewing);self.assertFalse(worker.enabled)


class HeatmapQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_overlay_has_no_shadow_and_only_heatmap_visibility_transitions_invalidate(self):
        window=OverlayWindow(ScreenRect(0,0,800,600),OverlaySettings())
        try:
            self.assertTrue(window.windowFlags()&Qt.WindowType.NoDropShadowWindowHint)
            latest=dict(version=1,rgba=np.zeros((8,10,4),np.uint8))
            with patch('overlay.platforms.invalidate_shadow') as invalidate:
                window.set_heatmap(None,enabled=False);invalidate.assert_not_called()
                window.set_heatmap(latest,enabled=True);invalidate.assert_called_once_with(window)
                window.set_heatmap(latest,enabled=True)
                window.set_heatmap(dict(latest,version=2),enabled=True)
                self.assertEqual(invalidate.call_count,1)
                window.set_heatmap(None,enabled=False);self.assertEqual(invalidate.call_count,2)
                window.set_heatmap(None,enabled=False);self.assertEqual(invalidate.call_count,2)
                window.set_heatmap(latest,enabled=True);self.assertEqual(invalidate.call_count,3)
        finally:window.hide();window.deleteLater();self.app.processEvents()

    def test_small_image_only_repaints_when_changed_and_ring_is_on_top(self):
        now=[10.]
        window=OverlayWindow(ScreenRect(0,0,800,600),replace(OverlaySettings(),label_autohide_s=0),clock=lambda:now[0])
        try:
            rgba=np.zeros((30,40,4),np.uint8);rgba[...]=[255,0,0,102]
            latest=dict(version=1,rgba=rgba)
            with patch.object(window,'update') as update:
                self.assertTrue(window.set_heatmap(latest,enabled=True));self.assertEqual(update.call_count,1)
                self.assertFalse(window.set_heatmap(latest,enabled=True));self.assertEqual(update.call_count,1)
            self.assertEqual(window._heatmap_image.size().width(),40)
            self.assertTrue(window.windowFlags()&Qt.WindowType.WindowTransparentForInput)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            window.set_display((400,300),(30,30),1.,'正常',label_kind='tracking')
            window.show();self.app.processEvents()
            image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0);window.render(image)
            # Ring color is green and must overwrite red density at its rim.
            rim=image.pixelColor(430,300);self.assertGreater(rim.green(),rim.red())
            self.assertLessEqual(image.pixelColor(300,300).alpha(),102)
            with patch.object(window,'update') as update:
                window.set_heatmap(latest,enabled=False);self.assertEqual(update.call_count,1)
                window.set_heatmap(latest,enabled=False);self.assertEqual(update.call_count,1)
        finally:window.hide();window.deleteLater();self.app.processEvents()

    def test_hidden_images_never_copy_or_invalidate_window_and_hiding_clears_once(self):
        window=OverlayWindow(ScreenRect(0,0,800,600),OverlaySettings())
        try:
            latest=dict(version=1,rgba=np.zeros((8,10,4),np.uint8))
            with patch.object(window,'update') as update:
                for version in range(1,10):window.set_heatmap(dict(latest,version=version),enabled=False)
                update.assert_not_called();self.assertIsNone(window._heatmap_image)
            window.set_heatmap(latest,enabled=True)
            with patch.object(window,'update') as update:
                window.set_heatmap(None,enabled=False);self.assertEqual(update.call_count,1)
                for version in range(10,20):window.set_heatmap(dict(latest,version=version),enabled=False)
                self.assertEqual(update.call_count,1)
            with patch.object(window,'_paint_heatmap') as draw:
                image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0);window.render(image)
                draw.assert_not_called()
        finally:window.hide();window.deleteLater();self.app.processEvents()

    def test_enlargement_interpolates_premultiplied_alpha_without_steps_or_dark_halo(self):
        window=OverlayWindow(ScreenRect(0,0,128,128),OverlaySettings())
        try:
            window._label_opacity=0.
            rgba=np.array([[[255,0,0,102],[255,255,0,0]],[[255,0,0,102],[255,255,0,0]]],np.uint8)
            window.set_heatmap(dict(version=1,rgba=rgba),enabled=True)
            self.assertEqual(window._heatmap_image.format(),QImage.Format.Format_ARGB32_Premultiplied)
            image=QImage(window.size(),QImage.Format.Format_ARGB32_Premultiplied);image.fill(0);window.render(image)
            samples=[image.pixelColor(x,20) for x in range(60,68)]
            self.assertTrue(all(0<c.alpha()<102 for c in samples))
            self.assertLessEqual(max(abs(a.alpha()-b.alpha()) for a,b in zip(samples,samples[1:])),3)
            self.assertTrue(all(c.red()>=250 for c in samples))
        finally:window.hide();window.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
