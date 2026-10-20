from dataclasses import replace
import math
import unittest
import numpy as np
from scipy import signal  # Keep compiled dependencies resident across engine sys.modules mocks.
from gaze_core.config import FilterSettings, OfflineFilterSettings
from gaze_core.filters import OneEuro, OneEuro2D, alpha, zero_phase_smooth

class FilterTests(unittest.TestCase):
    def test_constant_converges_and_axes_are_independent(self):
        f=OneEuro2D(FilterSettings(beta=0))
        f.reset([0,1],0)
        for i in range(1,301):out=f.update(np.array([1.,1.]),np.float64(i/30))
        np.testing.assert_allclose(out,[1,1],atol=1e-12)
        self.assertEqual(out[1],1)
    def test_real_uneven_intervals_and_reset(self):
        f=OneEuro(FilterSettings(min_cutoff=1,beta=0))
        self.assertEqual(f.update(np.float64(0),0),0)
        a=alpha(1,.1);self.assertAlmostEqual(f.update(1,.1),a)
        b=alpha(1,.35);self.assertAlmostEqual(f.update(1,.45),b+(1-b)*a)
        self.assertEqual(f.reset(-2),-2);self.assertEqual(f.update(-2,.9),-2)
    def test_adaptive_derivative_formula(self):
        s=FilterSettings(min_cutoff=.5,beta=.2,d_cutoff=2)
        f=OneEuro(s);f.update(0,0)
        cutoff=.5+.2*(alpha(2,.2)*5)
        self.assertAlmostEqual(f.update(1,.2),alpha(cutoff,.2))
    def test_reject_bad_time_without_corrupting_axes_and_no_clipping(self):
        f=OneEuro2D();f.update([-.2,1.4],1)
        for time in (1,.9):
            with self.assertRaises(ValueError):f.update([4,5],time)
        np.testing.assert_allclose(f.update([-.2,1.4],2),[-.2,1.4])
        for value in ([True,0],[np.nan,0],[0,np.inf]):
            with self.assertRaises(ValueError):f.update(value,3)
    def test_offline_no_phase_shift_and_original_timestamps(self):
        times=np.arange(401)/100
        values=np.column_stack([np.exp(-((times-2)/.05)**2),np.ones(len(times))])
        result=zero_phase_smooth(values,times)
        out=np.array(result['positions'])
        self.assertEqual(np.argmax(out[:,0]),200)
        np.testing.assert_allclose(out[:,0],out[::-1,0],atol=1e-10)
        np.testing.assert_allclose(out[:,1],1,atol=1e-12)
        self.assertEqual(result['timestamps_s'],times.tolist())
    def test_offline_irregular_short_and_gap_handling(self):
        times=np.cumsum(np.resize([.01,.02,.015],100))
        result=zero_phase_smooth([[1,2]]*100,times)
        np.testing.assert_allclose(result['positions'],[[1,2]]*100,atol=1e-10)
        result=zero_phase_smooth([[0,0],[1,1],[8,8]],[0,.03,1.0])
        self.assertEqual(result['positions'],[[0,0],[1,1],[8,8]])
        self.assertEqual([r['status'] for r in result['segments']],['too_short_unfiltered']*2)
        with self.assertRaises(ValueError):zero_phase_smooth([[0,0]]*2,[1,1])
        with self.assertRaises(ValueError):zero_phase_smooth([[0,0]]*20,np.arange(20)/10,OfflineFilterSettings(cutoff_hz=6))

if __name__=='__main__':unittest.main()
