"""Qt report controls. Local preparation/work happen off the gaze GUI tick."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import base64,json,time,threading
from pathlib import Path
from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QDesktopServices,QPixmap
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QDialogButtonBox,QScrollArea,QWidget,QInputDialog,QFileDialog,QMessageBox,QPlainTextEdit,QProgressBar
from gaze_core.reporting.ai import prepare,ConsentGate,interpret
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build,atomic


class SendPreview(QDialog):
    """The exact immutable request is prepared again after changing selections."""
    def __init__(self,artifact,parent=None):
        super().__init__(parent);self.artifact=artifact;self.prepared=None;self.setWindowTitle('AI发送前确认 · 可取消场景');self.resize(840,780)
        layout=QVBoxLayout(self);warning=QLabel('将发送所选场景的截图+热图缩略图和下列统计JSON。截图本身可能含可读文字及敏感内容；OCR JSON默认不发。不会发送摄像头画面、参与者编号、应用名或完整时间线。每次发送需重新确认。');warning.setWordWrap(True);layout.addWidget(warning)
        self.ocr=QCheckBox('额外发送所选AOI的OCR文字（默认关闭）');layout.addWidget(self.ocr)
        scroller=QScrollArea();scroller.setWidgetResizable(True);content=QWidget();self.rows=QVBoxLayout(content);scroller.setWidget(content);layout.addWidget(scroller)
        self.checks={};self.images={}
        for scene in artifact.data['scenes']:
            box=QCheckBox('场景 '+scene['id']);box.setChecked(True);self.rows.addWidget(box);image=QLabel();self.rows.addWidget(image)
            self.checks[scene['id']]=box;self.images[scene['id']]=image;box.toggled.connect(self.refresh)
        self.fields=QPlainTextEdit();self.fields.setReadOnly(True);self.fields.setMinimumHeight(220);layout.addWidget(self.fields)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认并发送一次');layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);self.ocr.toggled.connect(self.refresh);self.refresh()
    def refresh(self,*_):
        selected=[key for key,box in self.checks.items() if box.isChecked()]
        for key,label in self.images.items():label.setVisible(key in selected)
        try:
            self.prepared=prepare(self.artifact,selected,include_ocr=self.ocr.isChecked());p=self.prepared
            for key,url in p.thumbnails:
                pix=QPixmap();pix.loadFromData(base64.b64decode(url.split(',',1)[1]));self.images[key].setPixmap(pix.scaledToWidth(640,Qt.TransformationMode.SmoothTransformation))
            # The actual user content + prompt/schema is shown, with raster bytes
            # replaced only in this text view (the thumbnails above are exact).
            body=json.loads(p.body)
            def shrink(v):
                if isinstance(v,str) and v.startswith('data:image/'):return '[上方对应缩略图的内嵌字节]'
                if isinstance(v,list):return [shrink(x) for x in v]
                if isinstance(v,dict):return {k:shrink(x) for k,x in v.items()}
                return v
            self.fields.setPlainText(json.dumps(shrink(body),ensure_ascii=False,indent=2))
            estimate=json.loads(p.estimate_json);cost=estimate['estimated_budget_usd']
            self.status.setText(f'服务：{p.endpoint}\n模型：{p.model}\n预计输入 {estimate["input_tokens_estimate"]} tokens；输出上限 {estimate["output_token_cap"]}；本地预算 '+('未知' if cost is None else f'${cost:.4f}')+'\n'+estimate['definition']+'\n'+estimate['compat_price_note']);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except ValueError as exc:self.prepared=None;self.status.setText(str(exc));self.fields.clear();self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)


class ReportUI:
    def __init__(self,attention):
        self.attention=attention;self.controls=attention.controls;self.notice=attention.notice;self.options=attention.session.settings.report
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='attention-report');self.future=None;self.cancel=threading.Event();self.artifact=None;self.auto_seen=None;self.closed=False;self.gate=ConsentGate();self.kind=None;self.started=None;self._second=None;self._last_actions=None;self.editors=[];self.editor_revisions={}
        for name,callback in [('report_generate',self.generate),('report_edit',self.edit),('report_open',self.open),('report_ai',self.ai),('report_pdf',self.pdf),('report_aggregate',self.aggregate)]:self.controls.actions.connect(name,callback)
    def _status(self,text):self.controls.panel.report_status.setText(text)
    def _submit(self,kind,callback):
        if self.future is not None:return False
        self.cancel=threading.Event();self.kind=kind;self.started=time.monotonic();self._second=None;self.future=self.pool.submit(callback);self._status('正在'+kind+'…');return True
    def _version(self,path):
        variants=sorted(str(p.relative_to(path)) for p in (path/'derived').glob('*') if p.is_dir() and (p/'stats/session.json').exists())
        if not variants:return None
        names=['原始整理结果']+variants
        choice,ok=QInputDialog.getItem(self.controls.panel,'明确选择数据版本','恢复版本不会改写原M9验收：',names,0,False)
        if not ok:raise InterruptedError()
        return None if choice==names[0] else choice
    def generate(self):
        service=self.attention.service
        if self.future is not None or service.busy:return
        path=service.path
        if path is None:
            selected=QFileDialog.getExistingDirectory(self.controls.panel,'选择已结束的M9会话目录',str(service.root))
            if not selected:return
            path=Path(selected)
        try:version=self._version(path)
        except InterruptedError:return
        self._submit('生成离线报告',lambda:build(load_session(path,derived=version,options=self.options),options=self.options,cancelled=self.cancel.is_set))
    def aggregate(self):
        if self.future is not None:return
        paths=[]
        while True:
            selected=QFileDialog.getExistingDirectory(self.controls.panel,'选择刺激物会话；取消结束选择',str(self.attention.service.root))
            if not selected:break
            if Path(selected) not in paths:paths.append(Path(selected))
            if QMessageBox.question(self.controls.panel,'继续选择？',f'已选 {len(paths)} 个会话，是否再添加一个？')!=QMessageBox.StandardButton.Yes:break
        if not paths:return
        mapping={}
        # Metadata-only reads for legacy identifier prompts, images remain worker-owned.
        try:
            for path in paths:
                m=json.loads((path/'session.json').read_text(encoding='utf-8'))
                if not m.get('participant_id'):
                    value,ok=QInputDialog.getText(self.controls.panel,'旧会话事后匿名编号',path.name+'：同一人用同一编号')
                    if not ok:return
                    from gaze_core.reporting.participants import participant_id
                    mapping[m.get('session_id',path.name)]=participant_id(value)
        except Exception as exc:self._status('无法汇总：'+str(exc));return
        def work():
            from gaze_core.reporting.aggregate import aggregate
            data=aggregate([load_session(p,options=self.options) for p in paths],participant_map=mapping)
            return build(data,options=self.options,cancelled=self.cancel.is_set)
        self._submit('生成多人汇总',work)
    def open(self):
        if self.artifact is None:return
        ok=QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.artifact.html)))
        if ok:
            manifest_path=self.artifact.directory/'manifest.json'
            try:
                value=json.loads(manifest_path.read_text(encoding='utf-8'));value['browser_open_requested_ns']=time.perf_counter_ns();atomic(manifest_path,json.dumps(value,ensure_ascii=False).encode())
            except OSError:pass
        else:self._status('浏览器未能打开；可手动双击 '+str(self.artifact.html))
    def edit(self):
        if self.artifact is None or self.future is not None:return
        if self.artifact.data['kind']=='aggregate':self._status('请先编辑单个会话；同图模板会在再次汇总时复用');return
        from gaze_core.reporting.editor import EditorBridge
        self._submit('准备AOI编辑器',lambda:EditorBridge(self.artifact))
    def pdf(self):
        if self.artifact is None:return
        from gaze_core.reporting.pdf import export_pdf
        self._submit('导出PDF',lambda:export_pdf(self.artifact))
    def ai(self):
        if self.artifact is None or self.future is not None:return
        preview=SendPreview(self.artifact,self.controls.panel)
        if preview.exec()!=QDialog.DialogCode.Accepted or preview.prepared is None:self.gate.revoke();self._status('已取消AI发送；仅有统计报告，零请求');return
        prepared=preview.prepared;ticket=self.gate.approve(prepared);started=time.monotonic()
        self._submit('AI解读',lambda:interpret(self.artifact,prepared,ticket,self.gate,started=started,cancelled=self.cancel.is_set))
        self.started=started
    def poll(self):
        if self.closed:return
        service=self.attention.service
        for editor in self.editors:
            if editor.revision!=self.editor_revisions.get(editor,0):
                self.editor_revisions[editor]=editor.revision;self.artifact=editor.artifact;self._status('AOI已保存，当前报告已切换到新派生版本')
        if service.state=='ended' and service.mode=='stimulus' and service.path is not None and service.path!=self.auto_seen:
            if not self.options.auto_stimulus:self.auto_seen=service.path
            elif self.future is None:
                self.auto_seen=service.path
                path=service.path;self._submit('生成离线报告',lambda:build(load_session(path,options=self.options),options=self.options,cancelled=self.cancel.is_set))
        future=self.future
        if future is not None:
            second=int(time.monotonic()-self.started)
            timed_out=self.kind=='AI解读' and time.monotonic()-self.started>=self.options.ai_timeout_s
            if timed_out:self.cancel.set();self._status('AI总预算超时，统计报告保留；没有自动重试')
            if not timed_out and second!=self._second:
                self._second=second;self._status('正在'+self.kind+f' · {second} 秒'+(f' / 总预算 {self.options.ai_timeout_s:g} 秒；可继续使用电脑' if self.kind=='AI解读' else ''))
            if future.done():
                self.future=None
                try:
                    result=future.result()
                    if self.kind=='AI解读':
                        if result['ok']:self._status('AI中文解读已校验并更新；未自动重试');self.open()
                        else:self._status(result['reason'])
                    elif self.kind=='准备AOI编辑器':
                        self.editors.append(result);QDesktopServices.openUrl(QUrl(result.url));self._status('AOI编辑器已打开；保存可一键重新生成报告')
                    elif self.kind=='导出PDF':self._status('PDF已导出：'+str(result))
                    else:self.artifact=result;self._status('离线报告已生成：'+str(result.html));self.open()
                except Exception as exc:self._status('报告操作失败：'+('AI服务失败，统计报告保留' if self.kind=='AI解读' else str(exc)))
        key=(self.future is None,self.artifact is not None,service.busy)
        if key!=self._last_actions:
            self._last_actions=key
            for name in ('report_generate','report_aggregate'):self.controls.actions[name].setEnabled(key[0] and not key[2])
            for name in ('report_edit','report_open','report_pdf','report_ai'):self.controls.actions[name].setEnabled(key[0] and key[1])
    @property
    def busy_editing(self):return any(editor.lock.locked() for editor in self.editors)
    def cancel_editors(self,path):
        for editor in list(self.editors):
            if Path(editor.artifact.data['source_root'])==Path(path).resolve():editor.close();self.editors.remove(editor);self.editor_revisions.pop(editor,None)
    def close(self):
        for editor in self.editors:editor.close()
        self.closed=True;self.cancel.set();self.gate.revoke();self.pool.shutdown(wait=False,cancel_futures=True)
