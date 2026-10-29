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
    if not ok:raise RuntimeError('本地OCR失败：'+str(error))
    rows=[]
    for item in request.results():
        candidate=item.topCandidates_(1)[0];box=item.boundingBox();x=box.origin.x;y=1-box.origin.y-box.size.height
        rows.append(dict(text=str(candidate.string()),confidence=float(candidate.confidence()),
                         rect=[float(x),float(y),float(x+box.size.width),float(y+box.size.height)],source='vision'))
    return rows
