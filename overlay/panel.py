"""Ordinary, interactive controls. No camera access or calibration arithmetic."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QToolButton, QPlainTextEdit,QScrollArea


class ControlPanel(QWidget):
    def __init__(self, actions):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle('视线悬浮层 · 控制面板')
        outer=QVBoxLayout(self)
        self.scroll=QScrollArea(self);self.scroll.setWidgetResizable(True)
        content=QWidget();layout=QVBoxLayout(content)
        self.scroll.setWidget(content);outer.addWidget(self.scroll)
        self.mode = QLabel('未校准 · 等待相机条件')
        self.radius = QLabel('半径来源：配置示意值')
        self.recording = QLabel('录制：关闭')
        self.clicks = QLabel('点击采集：关闭 · 已记录 0 · 排除 0')
        self.autocal=QLabel('点击自动粗校准：关闭')
        self.demo_status=QLabel('Demo 练习修正：未开始')
        from gaze_core.heatmap import NOTICE
        self.heatmap=QLabel('热力图：关闭')
        self.heatmap_sigma=QLabel('核尺度 sigma：等待相机条件')
        self.heatmap_notice=QLabel(NOTICE)
        self.hotkeys = QLabel('系统热键：等待注册')
        self.notice = QLabel('托盘不可见时请保留此面板。关闭仅隐藏，退出请使用退出按钮或终端 Ctrl+C。')
        for label in (self.mode, self.radius, self.recording, self.clicks,self.autocal,self.demo_status, self.heatmap,self.heatmap_sigma,self.heatmap_notice,self.hotkeys, self.notice):
            label.setWordWrap(True); layout.addWidget(label)
        self.buttons = {}
        for name in ('demo','demo_practice','presentation','calibrate','check','load_web','reset','record','clicks','click_session','abort_click_session','autocal',
                     'heatmap_toggle','heatmap_view','heatmap_clear','heatmap_export','toggle','pause','web','quit'):
            button = QToolButton(self); button.setDefaultAction(actions[name])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            self.buttons[name] = button; layout.addWidget(button)
        self.results = QPlainTextEdit(self); self.results.setReadOnly(True)
        self.results.setPlaceholderText('完成核对后在此显示 A/B 逐目标汇总。')
        layout.addWidget(self.results)

    def present(self):
        from .platforms import present_panel
        present_panel(self)

    def closeEvent(self, event):
        event.ignore(); self.hide()


def summary_text(summary, distance=None):
    def f(value, unit=''):
        return '不可用' if value is None else f'{value:.3f}{unit}'
    lines = ['逐目标中位后等权汇总（未滤波、未裁剪）']
    lines.append('估计人脸到屏幕距离中位数：'+f(distance,' cm')+'（仅记录）')
    for group, values in summary['groups'].items():
        for region in ('all','center','edge'):
            result = values[region]
            title = {'all':'全部','center':'中央','edge':'边缘'}[region]
            lines.extend((f'{group} · {title}：{result["valid_targets"]}/{result["expected_targets"]} 点有厘米结果',
                          f'平均 {f(result["mean_cm"]," cm")}；P90 {f(result["p90_cm"]," cm")}',
                          f'≤1.5 cm {f(None if result["within15"] is None else result["within15"]*100,"%")}；≤2 cm {f(None if result["within2"] is None else result["within2"]*100,"%")}',
                          f'偏移 右/下为正：水平 {f(result["horizontal_cm"]," cm")}，垂直 {f(result["vertical_cm"]," cm")}',
                          f'水平缩放 {f(result["horizontal_scale"])}；垂直缩放 {f(result["vertical_scale"])}',
                          f'射线夹角 {f(result["angle_deg"],"°")}；yaw {f(result["yaw_deg"],"°")}；pitch {f(result["pitch_deg"],"°")}',
                          f'角度有效帧 {result["angle_frame_count"]}；缺 origin {result["missing_origin_frames"]}'))
    return '\n'.join(lines)
