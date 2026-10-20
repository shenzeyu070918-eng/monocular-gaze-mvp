import unittest

from overlay.label import LabelVisibility


class Clock:
    def __init__(self, now=10.0):
        self.now = now

    def __call__(self):
        return self.now


class LabelVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.label = LabelVisibility('等待画面', 4, 200, clock=self.clock)

    def test_startup_hold_then_linear_fade(self):
        self.assertEqual(self.label.text, '等待画面')
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 13.999
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 14.0
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 14.1
        self.assertAlmostEqual(self.label.opacity(), 0.5)
        self.clock.now = 14.2
        self.assertAlmostEqual(self.label.opacity(), 0.0)
        self.clock.now = 30.0
        self.assertEqual(self.label.opacity(), 0.0)

    def test_unchanged_text_never_restarts_hold_or_fade(self):
        for now in (11.0, 13.0, 14.0):
            self.clock.now = now
            self.assertFalse(self.label.set_text('等待画面'))
        self.clock.now = 14.1
        self.assertFalse(self.label.set_text('等待画面'))
        self.assertAlmostEqual(self.label.opacity(), 0.5)
        self.clock.now = 20.0
        self.assertFalse(self.label.set_text('等待画面'))
        self.assertEqual(self.label.opacity(), 0.0)

    def test_change_during_fade_redisplays_and_restarts_hold(self):
        self.clock.now = 14.1
        self.assertAlmostEqual(self.label.opacity(), 0.5)
        self.assertTrue(self.label.set_text('正在跟踪'))
        self.assertEqual(self.label.text, '正在跟踪')
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 18.1
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 18.2
        self.assertAlmostEqual(self.label.opacity(), 0.5)
        self.clock.now = 18.3
        self.assertAlmostEqual(self.label.opacity(), 0.0)

    def test_change_after_hidden_redisplays_and_restarts_hold(self):
        self.clock.now = 30.0
        self.assertEqual(self.label.opacity(), 0.0)
        self.assertTrue(self.label.set_text('未检测到人脸'))
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 34.0
        self.assertEqual(self.label.opacity(), 1.0)
        self.clock.now = 34.1
        self.assertAlmostEqual(self.label.opacity(), 0.5)
        self.clock.now = 34.2
        self.assertAlmostEqual(self.label.opacity(), 0.0)

    def test_blink_every_three_seconds_for_thirty_seconds_does_not_extend_startup(self):
        clock = Clock(0.)
        label = LabelVisibility('启动', 4, 200, debounce_s=1., clock=clock)
        for tenth in range(301):
            clock.now = tenth / 10
            blink = tenth >= 30 and tenth % 30 < 2
            label.set_text('眨眼' if blink else 'Python 摄像头',
                           kind='transient' if blink else 'tracking')
            opacity = label.opacity()
            if tenth <= 40:
                self.assertEqual(opacity, 1.)
            elif tenth == 41:
                self.assertAlmostEqual(opacity, .5)
            elif tenth >= 43:
                self.assertEqual(opacity, 0., clock.now)
            self.assertEqual(label.text, 'Python 摄像头')

    def test_sustained_no_face_appears_once_and_recovery_preserves_deadline(self):
        clock = Clock(0.)
        label = LabelVisibility('启动', 4, 200, clock=clock)
        label.set_text('跟踪', kind='tracking')
        clock.now = 6.
        self.assertEqual(label.opacity(), 0.)
        label.set_text('无人脸', kind='transient')
        self.assertTrue(label.pending)
        clock.now = 6.99
        self.assertEqual(label.opacity(), 0.)
        self.assertEqual(label.text, '跟踪')
        clock.now = 7.01
        self.assertEqual(label.opacity(), 1.)
        self.assertEqual(label.text, '无人脸')
        self.assertFalse(label.pending)
        clock.now = 8.
        label.set_text('无人脸', kind='transient')
        clock.now = 8.5
        label.set_text('跟踪', kind='tracking')
        self.assertEqual(label.text, '跟踪')
        clock.now = 11.11
        self.assertAlmostEqual(label.opacity(), .5)
        clock.now = 11.3
        self.assertEqual(label.opacity(), 0.)
        clock.now = 20.
        label.set_text('跟踪', kind='tracking')
        self.assertEqual(label.opacity(), 0.)

    def test_short_loss_and_recovery_cancel_pending_even_while_fading(self):
        clock = Clock(0.)
        label = LabelVisibility('启动', 4, 200, clock=clock)
        label.set_text('跟踪', kind='tracking')
        clock.now = 4.05
        label.set_text('无人脸', kind='transient')
        self.assertAlmostEqual(label.opacity(), .75)
        clock.now = 4.15
        label.set_text('跟踪', kind='tracking')
        self.assertFalse(label.pending)
        self.assertAlmostEqual(label.opacity(), .25)
        clock.now = 20.
        self.assertEqual(label.opacity(), 0.)
        label.set_text('无人脸', kind='transient')
        clock.now = 20.9
        label.set_text('跟踪', kind='tracking')
        clock.now = 22.
        self.assertEqual(label.opacity(), 0.)
        self.assertEqual(label.text, '跟踪')

    def test_immediate_states_and_same_text_user_actions_bypass_debounce(self):
        for text in ('启动', '暂停', '恢复', '出错', '屏幕变化', '退出'):
            clock = Clock(0.)
            label = LabelVisibility(text, 4, 200, debounce_s=10., clock=clock)
            clock.now = 20.
            self.assertEqual(label.opacity(), 0.)
            label.set_text('眨眼', kind='transient')
            label.set_text(text, force=True)
            self.assertFalse(label.pending)
            self.assertEqual(label.opacity(), 1.)
            clock.now = 23.
            self.assertFalse(label.set_text(text))
            clock.now = 24.3
            self.assertEqual(label.opacity(), 0.)

    def test_new_transient_status_restarts_pending_and_zero_debounce_is_immediate(self):
        clock = Clock(0.)
        label = LabelVisibility('启动', 4, 200, clock=clock)
        clock.now = 20.
        label.set_text('眨眼', kind='transient')
        clock.now = 20.8
        label.set_text('无人脸', kind='transient')
        clock.now = 21.1
        self.assertEqual(label.opacity(), 0.)
        clock.now = 21.9
        self.assertEqual(label.opacity(), 1.)
        self.assertEqual(label.text, '无人脸')
        clock.now = 30.
        label.set_text('无人脸', kind='transient')
        self.assertEqual(label.opacity(), 0.)
        immediate = LabelVisibility('启动', 4, 200, debounce_s=0, clock=clock)
        self.assertTrue(immediate.set_text('无人脸', kind='transient'))
        self.assertEqual(immediate.text, '无人脸')

    def test_zero_autohide_and_diagnostic_stay_visible(self):
        for autohide_s, diagnostic in ((0, False), (4, True), (0, True)):
            with self.subTest(autohide_s=autohide_s, diagnostic=diagnostic):
                label = LabelVisibility('等待画面', autohide_s, 200,
                                        diagnostic=diagnostic, clock=self.clock)
                self.clock.now += 1000
                self.assertEqual(label.opacity(), 1.0)
                self.assertTrue(label.set_text('正在跟踪'))
                self.clock.now += 1000
                self.assertEqual(label.opacity(), 1.0)

    def test_malformed_timings_rejected(self):
        malformed = (True, False, None, '4', [], complex(4, 0),
                     float('nan'), float('inf'), -float('inf'), -1,
                     10 ** 1000)
        for value in malformed:
            for field in ('autohide_s', 'fade_ms', 'debounce_s'):
                with self.subTest(field=field, value=value):
                    settings = {'autohide_s': 4, 'fade_ms': 200, field: value}
                    with self.assertRaises(ValueError):
                        LabelVisibility('等待画面', **settings, clock=self.clock)
        with self.assertRaises(ValueError):
            LabelVisibility('等待画面', 4, 0, clock=self.clock)
        with self.assertRaises(ValueError):
            self.label.set_text('错误类别', kind='unknown')


if __name__ == '__main__':
    unittest.main()
