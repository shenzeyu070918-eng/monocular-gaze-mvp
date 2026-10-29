import unittest
from dataclasses import replace
from gaze_core.planet import PlanetInteractionSettings
from gaze_core.config import load_settings

class ChargeSettingsTests(unittest.TestCase):
    def test_defaults_match_and_strengths_can_be_disabled(self):
        o=PlanetInteractionSettings();d=load_settings(use_local=False).galaxy.interaction
        names=[k for k in vars(o) if k.startswith('charge_')]
        for name in names:
            self.assertEqual(getattr(o,name),getattr(d,name))
            if name not in ('charge_response_s','charge_release_s'):self.assertEqual(getattr(replace(o,**{name:0}),name),0)
    def test_invalid_feedback_values_rejected(self):
        for field,value in (('charge_response_s',0),('charge_release_s',float('nan')),('charge_galaxy_contract',.3),('charge_ring_speed_gain',-1),('charge_planet_light_gain',True)):
            with self.assertRaises(ValueError):replace(PlanetInteractionSettings(),**{field:value})

if __name__=='__main__':unittest.main()
