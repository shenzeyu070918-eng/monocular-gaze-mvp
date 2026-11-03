"""On-device OCR entry point, safe to run in a spawned worker process."""
def recognize_file(path,languages=('zh-Hans','en-US')):
    import sys
    if sys.platform!='darwin':raise RuntimeError('此版本OCR仅实现macOS Vision；跨平台本地后端预留，未启用网络OCR。')
    import Vision as V
    from Foundation import NSURL
    request=V.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(V.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(list(languages));request.setUsesLanguageCorrection_(True)
    handler=V.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)),{})
    ok,error=handler.performRequests_error_([request],None)
    if not ok:raise RuntimeError('本地OCR失败：'+(str(error) if error is not None else 'Vision未提供错误详情；请在正常终端重试并检查本地Vision是否可用'))
    rows=[]
    for item in request.results():
        candidate=item.topCandidates_(1)[0];box=item.boundingBox();x=box.origin.x;y=1-box.origin.y-box.size.height
        rows.append(dict(text=str(candidate.string()),confidence=float(candidate.confidence()),
                         rect=[float(x),float(y),float(x+box.size.width),float(y+box.size.height)],source='vision'))
    return rows


class VisionOCR:
    def recognize(self,path,languages):return recognize_file(path,languages)


def _worker(connection,recognizer):
    try:
        while True:
            request=connection.recv()
            if request is None:return
            try:connection.send((True,recognizer(*request)))
            except Exception as exc:connection.send((False,str(exc)))
    except (EOFError,BrokenPipeError):pass
    finally:connection.close()


class OCRProcess:
    """One spawned local worker, with public terminate/join on timeout/delete."""
    def __init__(self,recognizer=None):
        self.recognizer=recognizer or VisionOCR().recognize;self.process=None;self.connection=None
    def _start(self):
        import multiprocessing
        context=multiprocessing.get_context('spawn');parent,child=context.Pipe()
        self.process=context.Process(target=_worker,args=(child,self.recognizer),name='attention-vision',daemon=True)
        self.process.start();child.close();self.connection=parent
    def recognize(self,path,languages,timeout,cancelled=lambda:False):
        import time
        if self.process is None:self._start()
        self.connection.send((str(path),languages));deadline=time.monotonic()+timeout
        while not self.connection.poll(.1):
            if cancelled():self.close();raise InterruptedError('OCR已取消')
            if time.monotonic()>=deadline:self.close();raise TimeoutError('本地OCR超时，工作进程已结束；可离线补跑')
            if not self.process.is_alive():self.close();raise RuntimeError('本地OCR工作进程意外退出')
        ok,result=self.connection.recv()
        if not ok:raise RuntimeError(result)
        return result
    def close(self):
        if self.process is not None:
            try:self.connection.send(None)
            except (BrokenPipeError,EOFError,OSError):pass
            self.process.join(.5)
            if self.process.is_alive():self.process.terminate();self.process.join(2)
            if self.process.is_alive():self.process.kill();self.process.join(2)
            self.connection.close();self.process=None;self.connection=None
