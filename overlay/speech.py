"""One asynchronous system speaker, cancelled when the Demo closes."""
import sys
from PySide6.QtCore import QObject,QProcess,Signal,QCoreApplication


class SystemSpeech(QObject):
    finished=Signal(bool)

    def __init__(self,parent=None,*,platform=sys.platform,process_factory=QProcess):
        super().__init__(parent)
        self.platform=platform;self.process_factory=process_factory;self.process=None;self._text=b''

    def speak(self,text):
        self.stop()
        try:
            if self.platform=='darwin':from .speech_macos import command
            elif self.platform=='win32':from .speech_windows import command
            else:raise RuntimeError('系统语音不可用')
            program,args=command();process=self.process_factory(self);self.process=process
            # Do not retain diagnostic output: it might contain supplied text.
            process.setStandardOutputFile(QProcess.nullDevice())
            process.setStandardErrorFile(QProcess.nullDevice())
            self._text=text.encode('utf-8')
            process.finished.connect(self._completed)
            process.errorOccurred.connect(self._failed)
            process.started.connect(self._started);process.start(program,args)
        except Exception:self.stop();self.finished.emit(False)

    def _started(self):
        process=self.sender()
        if self.process is process:
            process.write(self._text);process.closeWriteChannel();self._text=b''

    def _completed(self,code,status):
        self._finished(self.sender(),code==0 and status==QProcess.ExitStatus.NormalExit)

    def _failed(self,_):self._finished(self.sender(),False)

    def _finished(self,process,success):
        if self.process is not process:return
        self.process=None;self._text=b'';process.deleteLater();self.finished.emit(success)

    def stop(self):
        if self.process is not None:
            process,self.process=self.process,None
            self._text=b''
            process.started.disconnect(self._started);process.finished.disconnect(self._completed)
            process.errorOccurred.disconnect(self._failed)
            # The Demo may be deleted before an OS process acknowledges kill.
            # Let the application own it until finished, without a blocking wait.
            process.setParent(QCoreApplication.instance())
            process.finished.connect(process.deleteLater);process.kill()
            if process.state()==QProcess.ProcessState.NotRunning:process.deleteLater()
