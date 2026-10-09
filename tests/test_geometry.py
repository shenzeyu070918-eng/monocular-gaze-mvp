import unittest
import numpy as np
from geometry import intersect_screen, fit_affine


class GeometryTests(unittest.TestCase):
    def test_screen_center(self):
        np.testing.assert_allclose(intersect_screen([0,.10,.5],[0,0,-1],30,20,0),[.5,.5])

    def test_screen_right_is_camera_left(self):
        np.testing.assert_allclose(intersect_screen([0,.10,.5],[-.15,0,-.5],30,20,0),[1,.5])

    def test_parallel_and_away_rays_rejected(self):
        self.assertIsNone(intersect_screen([0,0,.5],[1,0,0],30,20))
        self.assertIsNone(intersect_screen([0,0,.5],[0,0,1],30,20))

    def test_offscreen_outputs_not_clipped(self):
        self.assertLess(intersect_screen([1,0,.5],[0,0,-1],30,20)[0],0)

    def test_affine_recovers_known_transform_on_held_out_points(self):
        transform=np.array([[.7,-.1],[.2,1.4],[.05,-.2]])
        samples=[{'raw':[x,y],'target':(np.array([x,y,1])@transform).tolist()} for x in [-.2,.4,1.2] for y in [-.5,.5,1.4]]
        got=np.asarray(fit_affine(samples))
        for p in [[.23,.72,1],[1.6,-.3,1]]:
            np.testing.assert_allclose(np.array(p)@got,np.array(p)@transform,atol=1e-10)

    def test_degenerate_calibration_rejected(self):
        with self.assertRaises(ValueError):
            fit_affine([{'raw':[.5,.5],'target':[i/9,i/9]} for i in range(9)])


if __name__=='__main__': unittest.main()
