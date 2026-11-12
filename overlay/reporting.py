"""Qt report controls. Local preparation/work happen off the gaze GUI tick."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import base64,json,time,threading
from pathlib import Path
from PySide6.QtCore import QUrl,Qt
from PySide6.QtGui import QDesktopServices,QPixmap
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QDialogButtonBox,QScrollArea,QWidget,QInputDialog,QFileDialog,QMessageBox,QPlainTextEdit,QProgressBar
from gaze_core.reporting.ai import prepare,ConsentGate,interpret
from gaze_core.reporting.pricing import image_mode
from gaze_core.reporting.data import load_session
from gaze_core.reporting.build import build,atomic


class SendPreview(QDialog):
    """The exact immutable request is prepared again after changing selections."""
    def __init__(self,artifact,parent=None):
        super().__init__(parent);self.artifact=artifact;self.prepared=None;self.setWindowTitle('AI发送前确认 · 可取消场景');self.resize(840,780)
        layout=QVBoxLayout(self);self.warning=QLabel('将发送所选场景的截图+热图缩略图和下列统计JSON。截图本身可能含可读文字及敏感内容；OCR JSON默认不发。不会发送摄像头画面、参与者编号、应用名或完整时间线。每次发送需重新确认。');self.warning.setWordWrap(True);layout.addWidget(self.warning)
        supported=not image_mode(artifact.options,False)[0]
        self.text_only=QCheckBox('纯文字模式：只发送统计JSON与区域短标签，不发送图片')
        self.text_only.setChecked(image_mode(artifact.options)[0]);self.text_only.setEnabled(supported);layout.addWidget(self.text_only)
        self.ocr=QCheckBox('额外发送所选AOI的OCR文字（默认关闭）');layout.addWidget(self.ocr)
        scroller=QScrollArea();scroller.setWidgetResizable(True);content=QWidget();self.rows=QVBoxLayout(content);scroller.setWidget(content);layout.addWidget(scroller)
        self.checks={};self.images={}
        for scene in artifact.data['scenes']:
            box=QCheckBox('场景 '+scene['id']);box.setChecked(True);self.rows.addWidget(box);image=QLabel();self.rows.addWidget(image)
            self.checks[scene['id']]=box;self.images[scene['id']]=image;box.toggled.connect(self.refresh)
        self.fields=QPlainTextEdit();self.fields.setReadOnly(True);self.fields.setMinimumHeight(220);layout.addWidget(self.fields)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认并发送一次');layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);self.ocr.toggled.connect(self.refresh);self.text_only.toggled.connect(self.refresh);self.refresh()
    def refresh(self,*_):
        selected=[key for key,box in self.checks.items() if box.isChecked()]
        pure=image_mode(self.artifact.options,self.text_only.isChecked())[0]
        if pure:
            self.ocr.blockSignals(True);self.ocr.setChecked(False);self.ocr.blockSignals(False)
        self.ocr.setEnabled(not pure)
        self.warning.setText('纯文字仅发送下列白名单统计和区域短标签（可能摘自OCR或手动名称），不发图片或完整OCR。' if pure else '发送所选场景截图+热图缩略图与统计JSON；截图内文字仍可能被读到。OCR JSON默认关闭。')
        for key,label in self.images.items():label.setVisible(key in selected and not pure)
        try:
            self.prepared=prepare(self.artifact,selected,include_ocr=self.ocr.isChecked(),text_only=self.text_only.isChecked());p=self.prepared
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
            self.status.setText(f'Provider：{p.provider_name}\n服务地址：{p.endpoint}\n模型：{p.model}\n模式：{p.mode_reason}\n计价时段：{estimate["period"]}\n预计输入 {estimate["input_tokens_estimate"]} tokens；输出上限 {estimate["output_token_cap"]}；本地预算 '+('未知' if cost is None else f'${cost:.4f}')+'\n'+estimate['definition']+'\n'+estimate['compat_price_note']);self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        except ValueError as exc:self.prepared=None;self.status.setText(str(exc));self.fields.clear();self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)


class ReportUI:
    def __init__(self,attention):
        self.attention=attention;self.controls=attention.controls;self.notice=attention.notice;self.options=attention.session.settings.report
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='attention-report')
        self.future=None;self.cancel=threading.Event();self.artifact=None;self.auto_seen=None;self.closed=False
        self.gate=ConsentGate();self.kind=None;self.started=None;self._second=None
        self.editors=[];self.editor_revisions={}
        self.selected=None;self._manual_selection=False;self._session_state=None
        self._sessions={};self._waiting=None;self._dialog_active=False;self._last_actions=None
        self._job_name=''
        for name,callback in [('report_generate',self.generate),('report_edit',self.edit),('report_open',self.open),('report_ai',self.ai),('report_pdf',self.pdf),('report_aggregate',self.aggregate)]:
            self.controls.actions.connect(name,callback)
        self.controls.panel.report_session.currentIndexChanged.connect(self._select)
        self._refresh_sessions()
        self._update_actions()

    def _status(self,text):
        self.controls.panel.report_status.setText(text)

    def _show_status(self,text):
        self._status(text)
        panel=self.controls.panel
        panel.scroll.ensureWidgetVisible(panel.report_status)

    def _finish(self,text,failed=False):
        self._show_status(text)
        self.notice(text,failed)

    @staticmethod
    def _label(path,metadata):
        try:
            when=datetime.strptime(path.name.rsplit('-',1)[0],'%Y%m%dT%H%M%S.%fZ')
            stamp=when.replace(tzinfo=timezone.utc).astimezone().strftime('%m-%d %H:%M:%S')
        except ValueError:
            stamp=path.name
        mode={'screen':'屏幕','stimulus':'刺激物'}.get(metadata.get('mode'),'会话')
        short_id=path.name.rsplit('-',1)[-1]
        identity=metadata.get('participant_id')
        number=f'{identity} · {short_id}' if identity else short_id
        return f'{stamp} · {mode} · {number}'

    def _refresh_sessions(self):
        service=self.attention.service
        active=Path(service.path).resolve() if service.path is not None else None
        state=(active,service.state)
        if state==self._session_state:return
        self._session_state=state
        sessions={}
        for path in sorted(Path(service.root).glob('*/session.json'),reverse=True):
            folder=path.parent.resolve()
            if folder.parent!=Path(service.root).resolve():continue
            try:metadata=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            if isinstance(metadata,dict) and (metadata.get('state') in ('ended','error') or folder==active):
                sessions[folder]=self._label(folder,metadata)
        if self._manual_selection and self.selected not in sessions and self.selected in self._sessions:
            if self.selected.parent!=Path(service.root).resolve() and (self.selected/'session.json').is_file():
                sessions[self.selected]=self._sessions[self.selected]
        self._sessions=sessions
        if not self._manual_selection or self.selected not in sessions:
            self.selected=active if active in sessions else next(iter(sessions),None)
            self._manual_selection=False
        combo=self.controls.panel.report_session
        combo.blockSignals(True)
        try:
            combo.clear()
            if not sessions:combo.addItem('暂无已整理会话')
            for path,label in sessions.items():combo.addItem(label,str(path))
            combo.addItem('选择其他会话目录…','browse')
            if self.selected is not None:combo.setCurrentIndex(combo.findData(str(self.selected)))
        finally:combo.blockSignals(False)

    def _select(self,*_):
        value=self.controls.panel.report_session.currentData()
        if value=='browse':
            self._browse_session()
            return
        self.selected=Path(value) if value else None
        self._manual_selection=True
        if self.artifact is not None and self.artifact.data['kind']=='aggregate':self.artifact=None
        self._status('已选择 '+self._sessions.get(self.selected,'会话')+'，可生成报告')
        self._update_actions()

    def _browse_session(self):
        panel=self.controls.panel
        combo=panel.report_session
        self._dialog_active=True
        self._update_actions()
        try:
            chosen=QFileDialog.getExistingDirectory(panel,'选择已结束的注意力会话目录',str(self.attention.service.root))
            if chosen:
                path=Path(chosen).resolve()
                metadata=json.loads((path/'session.json').read_text(encoding='utf-8'))
                if not isinstance(metadata,dict) or metadata.get('state') not in ('ended','error'):
                    raise ValueError('请先结束会话，再生成报告')
                label=self._label(path,metadata)
                self._sessions[path]=label
                if combo.findData(str(path))<0:combo.insertItem(combo.count()-1,label,str(path))
                self.selected=path
                self._manual_selection=True
                self._status(f'已选择 {label}，可生成报告')
        except (OSError,ValueError) as exc:
            self._finish(f'无法选择会话。{exc}',True)
        finally:
            combo.blockSignals(True)
            try:combo.setCurrentIndex(combo.findData(str(self.selected)) if self.selected else 0)
            finally:combo.blockSignals(False)
            self._dialog_active=False
            self._update_actions()

    def _current_artifact(self):
        if self.artifact is None:return None
        if self.selected is None:return self.artifact
        if self.artifact.data['kind']=='aggregate':return None
        return self.artifact if Path(self.artifact.data['source_root'])==self.selected else None

    @property
    def busy(self):
        return self.future is not None or self._waiting is not None or self._dialog_active

    def _update_actions(self):
        key=(self.busy,self._current_artifact() is not None,self.selected is not None)
        if key==self._last_actions:return
        self._last_actions=key
        busy,report,session=key
        self.controls.panel.report_session.setEnabled(not busy)
        self.controls.actions['report_generate'].setEnabled(not busy and session)
        self.controls.actions['report_aggregate'].setEnabled(not busy)
        for name in ('report_edit','report_open','report_pdf','report_ai'):
            self.controls.actions[name].setEnabled(not busy and report)

    def _submit(self,kind,callback,name):
        if self.busy:return False
        self.cancel=threading.Event();self.kind=kind;self.started=time.monotonic();self._second=None
        self._job_name=name
        self._show_status(f'正在{kind}…（{name}）')
        self.future=self.pool.submit(callback)
        self._update_actions()
        return True

    def _version(self,path):
        variants=sorted(str(p.relative_to(path)) for p in (path/'derived').glob('*') if p.is_dir() and (p/'stats/session.json').exists())
        if not variants:return None
        names=['原始整理结果']+variants
        choice,ok=QInputDialog.getItem(self.controls.panel,'明确选择数据版本','恢复版本不会改写原M9验收：',names,0,False)
        if not ok:raise InterruptedError()
        return None if choice==names[0] else choice
    def generate(self):
        if self.busy:return
        self._refresh_sessions()
        path=self.selected
        if path is None:
            self._finish('无法生成报告。请先录制并结束一个会话。',True)
            return
        service=self.attention.service
        if service.path is not None and path==Path(service.path).resolve() and service.busy:
            if service.state=='finalizing':
                self._waiting=path
                self._show_status(f'正在整理会话，完成后生成报告…（{self._sessions.get(path,path.name)}）')
                self._update_actions()
            else:
                self._finish('无法生成报告。请先结束当前会话。',True)
            return
        self._generate(path,choose_version=True)

    def _generate(self,path,*,choose_version):
        name=self._sessions.get(path,path.name)
        self._show_status(f'正在生成报告…（{name}）')
        self._dialog_active=True
        self._update_actions()
        try:
            version=self._version(path) if choose_version else None
        except InterruptedError:
            self._status(f'已取消生成报告（{name}）')
            return
        finally:
            self._dialog_active=False
            self._update_actions()
        self._submit('生成报告',lambda:build(load_session(path,derived=version,options=self.options),options=self.options,cancelled=self.cancel.is_set),name)

    def aggregate(self):
        if self.busy:return
        self._dialog_active=True
        self._update_actions()
        try:self._aggregate()
        finally:
            self._dialog_active=False
            self._update_actions()

    def _aggregate(self):
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
        self._dialog_active=False
        self._submit('生成多人汇总',work,'多人汇总')
    def open(self):
        artifact=self._current_artifact()
        if artifact is not None and not self.busy:
            self._open_report(artifact,self._artifact_name(artifact))

    def _artifact_name(self,artifact):
        if artifact.data['kind']=='aggregate':return '多人汇总'
        return self._sessions.get(Path(artifact.data['source_root']),artifact.data['session_id'])

    def _open_report(self,artifact,name):
        if not artifact.html.is_file():
            self._finish(f'报告文件已找不到（{name}）',True)
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(artifact.html))):
            manifest_path=artifact.directory/'manifest.json'
            try:
                value=json.loads(manifest_path.read_text(encoding='utf-8'))
                value['browser_open_requested_ns']=time.perf_counter_ns()
                atomic(manifest_path,json.dumps(value,ensure_ascii=False).encode())
            except (OSError,ValueError):pass
            self._finish(f'已完成，已在浏览器打开（{name}）')
        else:
            self._finish(f'报告已完成，但浏览器未能打开（{name}）。可手动打开 {artifact.html}',True)

    def edit(self):
        artifact=self._current_artifact()
        if artifact is None or self.busy:return
        if artifact.data['kind']=='aggregate':
            self._status('请先编辑单个会话，同图模板会在再次汇总时复用')
            return
        from gaze_core.reporting.editor import EditorBridge
        self._submit('准备AOI编辑器',lambda:EditorBridge(artifact),self._artifact_name(artifact))

    def pdf(self):
        artifact=self._current_artifact()
        if artifact is None or self.busy:return
        from gaze_core.reporting.pdf import export_pdf
        self._submit('导出 PDF',lambda:export_pdf(artifact),self._artifact_name(artifact))

    def ai(self):
        artifact=self._current_artifact()
        if artifact is None or self.busy:return
        name=self._artifact_name(artifact)
        self._show_status(f'正在准备 AI 发送预览…（{name}）')
        self._dialog_active=True
        self._update_actions()
        try:
            preview=SendPreview(artifact,self.controls.panel)
            if preview.exec()!=QDialog.DialogCode.Accepted or preview.prepared is None:
                self.gate.revoke()
                self._status(f'已取消AI发送，零请求（{name}）')
                return
            prepared=preview.prepared
        except Exception:
            self._finish(f'AI 预览失败（{name}）',True)
            return
        finally:
            self._dialog_active=False
            self._update_actions()
        ticket=self.gate.approve(prepared);started=time.monotonic()
        self._submit('AI 解读',lambda:interpret(artifact,prepared,ticket,self.gate,started=started,cancelled=self.cancel.is_set),name)
        self.started=started

    def poll(self):
        if self.closed:return
        service=self.attention.service
        # Keep the operation pinned even if another recording finishes while it runs.
        if not self.busy:self._refresh_sessions()
        for editor in self.editors:
            if editor.revision!=self.editor_revisions.get(editor,0):
                self.editor_revisions[editor]=editor.revision
                if self.future is None and (self.selected is None or Path(editor.artifact.data['source_root'])==self.selected):
                    self.artifact=editor.artifact
                    self._status('AOI已保存，当前报告已切换到新派生版本')
        if self._waiting is not None and not service.busy:
            path=self._waiting;self._waiting=None
            if service.state=='error' and service.path is not None and Path(service.path).resolve()==path:
                self._finish('会话整理失败，无法生成报告。'+(getattr(service,'error',None) or '请查看会话状态。'),True)
            else:
                self.auto_seen=path
                self._generate(path,choose_version=False)
        if service.state=='ended' and service.mode=='stimulus' and service.path is not None and not self.busy:
            path=Path(service.path).resolve()
            if path!=self.auto_seen:
                self.auto_seen=path
                if self.options.auto_stimulus and self.selected==path:self._generate(path,choose_version=False)
        future=self.future
        if future is not None:
            second=int(time.monotonic()-self.started)
            timed_out=self.kind=='AI 解读' and time.monotonic()-self.started>=self.options.ai_timeout_s
            if timed_out:self.cancel.set()
            if second!=self._second and not future.done():
                self._second=second
                self._status(f'正在{self.kind}… {second} 秒（{self._job_name}）')
            if future.done():
                self.future=None
                try:
                    result=future.result()
                    if self.kind=='AI 解读':
                        if result['ok']:self._open_report(self.artifact,self._job_name)
                        else:self._finish(f'{result["reason"]}（{self._job_name}）',True)
                    elif self.kind=='准备AOI编辑器':
                        self.editors.append(result)
                        opened=QDesktopServices.openUrl(QUrl(result.url))
                        self._finish('AOI编辑器已打开' if opened else '浏览器未能打开AOI编辑器',not opened)
                    elif self.kind=='导出 PDF':
                        opened=QDesktopServices.openUrl(QUrl.fromLocalFile(str(result)))
                        if opened:
                            self._finish(f'已完成，PDF 已导出并打开（{self._job_name}）')
                        else:
                            self._finish(f'PDF 已导出，系统未能打开（{self._job_name}）。可手动打开 {result}',True)
                    else:
                        self.artifact=result
                        if result.data['kind']=='aggregate':
                            self.selected=None
                            combo=self.controls.panel.report_session
                            combo.blockSignals(True)
                            try:
                                combo.addItem('多人汇总')
                                combo.setCurrentIndex(combo.count()-1)
                            finally:combo.blockSignals(False)
                        self._open_report(result,self._job_name)
                except Exception as exc:
                    reason='AI服务失败，统计报告保留' if self.kind=='AI 解读' else str(exc)
                    self._finish(f'{self.kind}失败（{self._job_name}）。{reason}',True)
        self._update_actions()

    @property
    def busy_editing(self):return any(editor.lock.locked() for editor in self.editors)
    def cancel_editors(self,path):
        for editor in list(self.editors):
            if Path(editor.artifact.data['source_root'])==Path(path).resolve():editor.close();self.editors.remove(editor);self.editor_revisions.pop(editor,None)
    def close(self):
        for editor in self.editors:editor.close()
        self.closed=True;self.cancel.set();self.gate.revoke();self.pool.shutdown(wait=False,cancel_futures=True)
