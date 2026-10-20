from dataclasses import replace
import unittest
import numpy as np
from gaze_core.capture import Prediction
from gaze_core.config import OverlaySettings
from overlay.state import RingState


def result(sequence,ns,point=(.2,.3),status='tracking',**display):
    return Prediction(sequence,ns,ns+99,dict(valid=status=='tracking',status=status,
                      display=dict(position=point,timestamp_source='capture_ns',**display)))


class RingTests(unittest.TestCase):
    def setUp(self):
        self.options=OverlaySettings();self.ring=RingState(self.options)

    def test_raw_mode_numpy_unclipped_and_saccade_immediate(self):
        self.ring.accept(result(1,0,[np.float64(-.2),np.float64(1.2)]),0)
        self.assertEqual(self.ring.view(0).position,(-.2,1.2))
        self.assertTrue(self.ring.view(0).tracking)
        self.assertIn('未校准',self.ring.view(0).label)
        self.ring.accept(result(2,10_000_000,[.9,.1],state='saccade'),10_000_000)
        self.assertEqual(self.ring.view(10_000_000).position,(.9,.1))

    def test_invalid_fades_previous_position_and_does_not_restart(self):
        self.ring.accept(result(1,0),0)
        self.ring.accept(result(2,50_000_000,[99,99],status='blink'),50_000_000)
        view=self.ring.view(150_000_000)
        self.assertFalse(view.tracking)
        self.assertEqual(view.position,(.2,.3));self.assertAlmostEqual(view.opacity,self.options.opacity/2)
        self.ring.accept(result(3,200_000_000,[8,8],status='no_face'),200_000_000)
        self.assertIsNone(self.ring.view(250_000_000).position)
        self.ring.accept(result(4,260_000_000,[.7,.5]),260_000_000)
        self.assertEqual(self.ring.view(260_000_000).position,(.7,.5))
        self.assertTrue(self.ring.view(260_000_000).tracking)

    def test_age_uses_capture_not_completion_and_clear_prevents_reappearance(self):
        stale=replace(result(1,0),completed_ns=999_000_000)
        self.ring.accept(stale,999_000_000)
        self.assertIsNone(self.ring.view(999_000_000).position)
        self.ring.accept(result(2,1_000_000_000),1_000_000_000)
        self.assertIsNone(self.ring.view(1_500_000_000).position)
        self.assertFalse(self.ring.view(1_500_000_000).tracking)
        self.ring.clear();self.assertIsNone(self.ring.view(1_500_000_000).position)

    def test_wrong_clock_future_duplicate_and_bad_numbers_rejected(self):
        for item in (replace(result(1,0),data=dict(valid=True,status='tracking',display=dict(position=[.2,.3],timestamp_source='browser_capture_ms'))),
                     result(1,100),result(1,0,[True,0]),result(1,0,[np.inf,0])):
            ring=RingState(self.options);ring.accept(item,0)
            self.assertIsNone(ring.view(0).position)
        self.ring.accept(result(2,100,[.2,.3]),100)
        self.ring.accept(result(1,0,[.9,.9]),110)
        self.assertEqual(self.ring.view(110).position,(.2,.3))


if __name__=='__main__':unittest.main()
