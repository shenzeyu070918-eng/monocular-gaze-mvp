"""Existing synchronous web contract with an injected model, no camera access."""
import base64
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

import app as web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.result = {'valid':True, 'status':'tracking', 'raw':[-.2,1.2], 'inference_ms':1,
                       'landmarks':[], 'distance_cm':50, 'origin_m':[0,0,.5],
                       'gaze_vector':[0,0,-1], 'head_degrees':[0,0,0], 'eye_openness':.3}
        self.engine = SimpleNamespace(predict=Mock(return_value=self.result), close=Mock(),
                                      parameters=123, device='cuda', device_name='FAKE GPU')
        self.patch = patch.multiple(web, engine=self.engine, state={'ready':True,'error':None})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(web.app)
        self.addCleanup(self.client.close)
        ok, encoded = cv2.imencode('.jpg', np.zeros((8,8,3), dtype=np.uint8))
        self.assertTrue(ok)
        self.image = base64.b64encode(encoded).decode()

    def test_frame_contract_raw_coordinates_and_input_forwarding(self):
        geometry = {'width_cm':30.2,'height_cm':19.6,'top_cm':.2,'focal_scale':1.1,'horizontal_cm':0.,'measured':False}
        response = self.client.post('/api/frame', json={'image':self.image, 'grayscale':True,'geometry':geometry})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.result)
        image, passed_geometry, gray = self.engine.predict.call_args.args
        self.assertEqual(image.shape, (8,8,3))
        self.assertEqual(passed_geometry.model_dump(), geometry)
        self.assertTrue(gray)

    def test_existing_invalid_input_and_prediction_error_responses(self):
        for image in ['%%%', base64.b64encode(b'not an image').decode()]:
            self.assertEqual(self.client.post('/api/frame',json={'image':image}).status_code, 400)
        self.engine.predict.side_effect = RuntimeError('fake error')
        result = self.client.post('/api/frame',json={'image':self.image}).json()
        self.assertEqual(result, {'valid':False,'status':'prediction_error','inference_ms':0})
        web.state['ready'] = False
        self.assertEqual(self.client.post('/api/frame',json={'image':self.image}).status_code, 503)

    def test_calibration_and_static_web_still_available(self):
        samples = [{'raw':[x,y],'target':[x,y]} for x in [0,.5,1] for y in [0,.5,1]]
        response = self.client.post('/api/calibrate',json={'samples':samples,'fullscreen':True})
        self.assertEqual(response.status_code, 200)
        np.testing.assert_allclose(response.json()['matrix'], [[1,0],[0,1],[0,0]], atol=1e-12)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/app.js').status_code, 200)

    def test_status_reports_actual_device(self):
        with patch.object(web, 'Engine', return_value=self.engine):
            web.load_engine()
        response = self.client.get('/api/status').json()
        self.assertEqual((response['device'],response['device_name']), ('CUDA','FAKE GPU'))


if __name__ == '__main__': unittest.main()
