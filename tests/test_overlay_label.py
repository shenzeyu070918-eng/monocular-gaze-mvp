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
            for field in ('autohide_s', 'fade_ms'):
                with self.subTest(field=field, value=value):
                    settings = {'autohide_s': 4, 'fade_ms': 200, field: value}
                    with self.assertRaises(ValueError):
                        LabelVisibility('等待画面', **settings, clock=self.clock)
        with self.assertRaises(ValueError):
            LabelVisibility('等待画面', 4, 0, clock=self.clock)


if __name__ == '__main__':
    unittest.main()
