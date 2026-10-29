"""Minimal M9 controls and stimulus window. All analysis stays off Qt."""
from pathlib import Path
import time
from PySide6.QtCore import Qt,QTimer,Signal,QPoint,QRectF
from PySide6.QtGui import QPainter,QImage,QImageReader
from PySide6.QtWidgets import QLabel,QWidget,QFileDialog,QMessageBox
from gaze_core.attention.session import AttentionSession
from gaze_core.attention.stimulus import StimulusMapping
from gaze_core.demo_coordinates import DemoCoordinates
from gaze_core.heatmap import kernel_scale


class StimulusWindow(QWidget):
    ready=Signal(object);cancelled=Signal()
    def __init__(self,path,qscreen,screen):
        super().__init__()
        import hashlib
        reader=QImageReader(str(path));reader.setAutoTransform(True);self.image=reader.read()
        self.source_sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.screen_rect=screen;self.mapping=None;self._announced=False
        if self.image.isNull():raise ValueError('无法读取PNG/JPG图片')
        self.setWindowTitle('屏幕注意力 · 图片刺激物（Esc结束）')
        self.winId();self.windowHandle().setScreen(qscreen);self.setGeometry(qscreen.geometry())
        self.showFullScreen();QTimer.singleShot(400,self._ready)
    def _ready(self):
        p=self.mapToGlobal(QPoint(0,0));c=DemoCoordinates(self.screen_rect,window_origin=(p.x(),p.y()),window_size=(self.width(),self.height()),fullscreen=self.isFullScreen())
        if not c.ready:self.ready.emit(ValueError(c.reason));return
        self.mapping=StimulusMapping(c,(self.image.width(),self.image.height()));self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),Qt.GlobalColor.black)
        if self.mapping:painter.drawImage(QRectF(*self.mapping.image_rect),self.image)
        painter.end()
        if self.mapping is not None and not self._announced:
            self._announced=True;QTimer.singleShot(0,lambda:self.ready.emit(self.mapping))
    def resizeEvent(self,event):
        if self.mapping is not None:
            # A changed fit invalidates binding. End this session rather than
            # silently stretching earlier gaze into a new image geometry.
            self.cancelled.emit()
        super().resizeEvent(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.cancelled.emit()
        else:super().keyPressEvent(event)
    def closeEvent(self,event):self.cancelled.emit();event.accept()


class AttentionUI:
    def __init__(self,session,window,controls,notice):
        self.session,self.window,self.controls,self.notice=session,window,controls,notice
        def backend():
            import sys
            if sys.platform!='darwin':raise RuntimeError('Windows截屏尚未实现')
            from .attention_macos import MacScreenCapture
            return MacScreenCapture(self.service.screen,session.settings.attention.excluded_apps,request_permission=True,timeout_s=session.settings.attention.screenshot_timeout_s)
        self.service=AttentionSession(session.settings,session.screen,backend_factory=backend);session.attention=self.service
        self.stimulus=None;self._pending_stimulus=False;self._last=None;self._next_ui=0;self._last_error=None;self._closing_stimulus=False;self.guide=None;self._profile_key=None;self._profile_matches=False
        flags=Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowTransparentForInput|Qt.WindowType.WindowDoesNotAcceptFocus|Qt.WindowType.NoDropShadowWindowHint
        self.badge=QLabel('',None,flags);self.badge.setStyleSheet('background:#9d2435;color:white;padding:8px 14px;font-size:14px;border-radius:6px;')
        self.badge.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        actions=controls.actions
        actions.connect('attention_guide',self.open_guide)
        for name,callback in [('attention_start',self.start_screen),('attention_stimulus',self.start_stimulus),('attention_pause',self.service.pause),('attention_end',self.end),('attention_delete',self.delete)]:actions.connect(name,callback)
        actions['attention_ring'].setChecked(not session.settings.attention.hide_ring)
        actions['attention_ring'].setEnabled(True)

    def _kernel(self):
        s=self.session;radius=s.profiles.radius
        return kernel_scale(s.settings.geometry,(s.screen.width,s.screen.height),radius or s.settings.overlay.radius_cm,s.settings.overlay.fallback_radius_px,
                            'matching_B_check' if radius is not None else 'configured_cm')

    def _require_ready(self):
        import sys
        if sys.platform!='darwin':raise RuntimeError('M9截屏与本地OCR当前只实现macOS；Windows后端尚未实现。')
        s=self.session
        if s._active_group!='B' or s.profiles.profile is None or s.profiles.profile.get('pipeline')!='python':raise ValueError('请先启用匹配的 Python 九点档案')

    def _start(self,**kwargs):
        self._require_ready()
        s=self.session
        if not s._running or s.latest is None or s.active or s._demo_active or s.clicks is not None and s.clicks.active:raise ValueError('请先恢复摄像头并结束当前交互任务')
        if s._active_group!='B':raise ValueError('请先启用匹配的 Python 九点校准，粗校准不适用。')
        self.service.screen=s.screen
        self.service.start(s.profiles.profile,s.profiles.conditions,self._kernel(),**kwargs)
        if kwargs.get('mode')=='stimulus':self.controls.panel.hide()
        self._last=None

    def open_guide(self):
        try:
            self._require_ready()
            if self.service.busy:raise ValueError('请先结束现有注意力会话')
            if self.session._active_group!='B' or self.session.profiles.profile is None:raise ValueError('请先完成匹配的Python九点')
            if self.guide is not None:self.guide.close()
            from .attention_guide import AttentionGuide
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            self.service.begin_baseline()
            self.guide=AttentionGuide(self.session.screen)
            guide_started=[None]
            def message(event):
                if event['kind']=='error':self.notice('引导基线失败：'+event['error'],True);return
                if event['kind']=='begin':
                    try:self._start()
                    except Exception as exc:self.notice('引导会话未开始：'+str(exc),True);return
                    guide_started[0]=self.service.started_ns
                    self.service._put(('acceptance',dict(type='acceptance',kind='cpu_baseline',time_ns=time.perf_counter_ns(),measurement=dict(event['cpu'],latency=self.service.baseline_latency()))))
                elif guide_started[0]!=self.service.started_ns:return
                elif event['kind']=='end':
                    self.service._put(('acceptance',dict(type='acceptance',kind='cpu_recording',time_ns=event['time_ns'],measurement=event['cpu'])))
                    self.end()
                else:self.service._put(('acceptance',event))
            self.guide.message.connect(message)
            self.controls.panel.hide()
            if not QDesktopServices.openUrl(QUrl(self.guide.url)):raise RuntimeError('无法打开本地验收页')
        except Exception as exc:self.notice('无法开始引导：'+str(exc),True)

    def start_screen(self):
        try:self._start()
        except Exception as exc:self.notice('无法开始注意力会话：'+str(exc),True)

    def start_stimulus(self):
        if self.service.busy:return
        try:self._require_ready()
        except Exception as exc:self.notice(str(exc),True);return
        path,_=QFileDialog.getOpenFileName(self.controls.panel,'选择本地刺激图片（不截屏）','','图片 (*.png *.jpg *.jpeg)')
        if not path:return
        try:
            self.controls.panel.hide()
            self._pending_stimulus=True
            self.stimulus=StimulusWindow(path,self.controls.screen,self.session.screen)
            def ready(mapping):
                self._pending_stimulus=False
                if isinstance(mapping,Exception):self.notice(str(mapping),True);self._close_stimulus();return
                try:self._start(mode='stimulus',stimulus=dict(path=path,sha256=self.stimulus.source_sha,screen_rect=mapping.normalized_rect(),kernel=mapping.kernel(self._kernel())))
                except Exception as exc:self._close_stimulus();self.notice('刺激物会话未开始：'+str(exc),True)
            self.stimulus.ready.connect(ready);self.stimulus.cancelled.connect(self.end)
        except Exception as exc:
            self._pending_stimulus=False;self.notice('无法显示图片：'+str(exc),True)

    def _close_stimulus(self):
        if self.stimulus is not None and not self._closing_stimulus:
            self._closing_stimulus=True;old=self.stimulus;self.stimulus=None;old.close();old.deleteLater();self._closing_stimulus=False

    def end(self):
        self.service.stop();self._close_stimulus()

    def delete(self):
        if self.service.path is None:return
        answer=QMessageBox.question(self.controls.panel,'删除整个注意力会话','删除本会话全部截图、OCR与数值数据？此操作不能撤销。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Yes:self._close_stimulus();self.service.delete()

    def poll(self,*,active,screen_changed,demo_active,now_ns):
        worker=self.service;s=self.session
        if worker.active:
            profile=s.profiles.profile;key=(worker.started_ns,id(profile))
            if key!=self._profile_key:
                self._profile_key=key
                expected=(worker._metadata or {}).get('profile',{})
                self._profile_matches=(profile is not None and all(profile.get(k)==expected.get(k) for k in ('calibration_id','matrix_id','conditions','coordinate_version','matrix','latest_check')))
            reason=('screen_geometry_changed' if screen_changed or s.screen!=worker.screen else
                    'camera_paused' if not active else 'interactive_task' if s.active or demo_active else
                    'profile_changed_restart_session' if not self._profile_matches or s._active_group!='B' else None)
            worker.block(reason)
        hidden=worker.active and not self.controls.actions['attention_ring'].isChecked()
        self.window.set_attention_ring_hidden(hidden)
        key=(worker.state,worker.error,worker.scene_count,str(worker.path),worker.blocked)
        if key==self._last and (not worker.active or now_ns<self._next_ui):return
        self._last=key;self._next_ui=now_ns+250000000
        seconds=0 if worker.started_ns is None else max(0,int((now_ns-worker.started_ns)/1e9))
        names={'idle':'关闭','starting':'正在准备','recording':'录制中','paused':'已暂停','finalizing':'正在整理（OCR后台）','ended':'已结束','error':'失败（已停止）','deleting':'正在删除'}
        text='注意力：'+names.get(worker.state,worker.state)+f' · {worker.scene_count} 场景'
        if worker.active:text+=f' · {seconds}s · '+('图片刺激物，不截屏' if worker.mode=='stimulus' else '仅本机截屏')
        reason=worker.blocked or (worker._effective_state if worker.state=='paused' else None)
        reasons={'own_ui':'本程序界面在前台，切到要分析的应用后自动恢复','excluded_app':'排除名单应用在前台','foreground_or_filter_changed':'应用切换中，等待安全采样','screen_geometry_changed':'屏幕变化，请重新九点后开启新会话','profile_changed_restart_session':'九点条件变化，请结束后新开会话','camera_conditions_changed_restart_session':'实际相机条件变化，请重新匹配九点并新开会话','camera_paused':'摄像头暂停','interactive_task':'校准/核对/Demo期间暂停','user_paused':'用户暂停'}
        if reason:text+=' · '+reasons.get(reason,reason)
        if worker.error:text+=' · '+worker.error
        if worker.path:text+='\n'+str(worker.path)
        self.controls.panel.attention.setText(text)
        actions=self.controls.actions
        ready=active and s.latest is not None and not s.active and not demo_active and not worker.busy
        for name in ('attention_guide','attention_start','attention_stimulus'):actions[name].setEnabled(ready)
        actions['attention_pause'].setEnabled(worker.active);actions['attention_end'].setEnabled(worker.active)
        actions['attention_delete'].setEnabled(worker.path is not None)
        if worker.active and not screen_changed:
            badge_text=('● 图片分析 ' if worker.mode=='stimulus' else '● 屏幕分析 ')+names.get(worker.state,worker.state)+f' {seconds}s'
            self.badge.setText(badge_text);self.badge.adjustSize()
            rect=s.screen;self.badge.move(rect.x+(rect.width-self.badge.width())//2,rect.y+46)
            if not self.badge.isVisible():
                self.badge.winId()
                from .platforms import configure_window
                configure_window(self.badge);self.badge.show()
        else:
            self.badge.hide()
            if not self._pending_stimulus:self._close_stimulus()
        if worker.error and worker.error!=self._last_error:self._last_error=worker.error;self.notice('注意力会话已停止：'+worker.error,True)
        if worker.state=='ended' and worker.summary and worker.summary.get('warnings'):
            self.controls.panel.attention.setText(text+'\n整理警告：'+'；'.join(worker.summary['warnings']))

    def close_ui(self):
        self.badge.hide();self._close_stimulus();self.window.set_attention_ring_hidden(False)
        if self.guide is not None:self.guide.close();self.guide=None
