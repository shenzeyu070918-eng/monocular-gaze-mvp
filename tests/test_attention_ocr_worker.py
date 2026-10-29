import time,unittest
from gaze_core.attention.ocr import OCRProcess


def fake_ocr(path,languages):return [dict(text='本地OCR',rect=[0,0,1,1],confidence=1.)]
def slow_ocr(path,languages):time.sleep(10);return []

class OCRWorkerTests(unittest.TestCase):
    def test_spawned_numeric_text_only_and_clean_exit(self):
        worker=OCRProcess(fake_ocr)
        try:
            result=worker.recognize('unused',('zh-Hans',),10);self.assertEqual(result[0]['text'],'本地OCR');process=worker.process
        finally:worker.close()
        self.assertFalse(process.is_alive())
    def test_timeout_terminates_worker(self):
        worker=OCRProcess(slow_ocr)
        try:
            with self.assertRaises(TimeoutError):worker.recognize('unused',(),.1)
            self.assertIsNone(worker.process)
        finally:worker.close()
