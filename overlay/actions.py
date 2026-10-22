"""One set of Qt actions shared by menus and the ordinary control panel."""
from PySide6.QtGui import QAction


class OverlayActions:
    LABELS = {
        'panel': '打开控制面板', 'calibrate': '开始九点校准', 'check': '精度核对（A/B）',
        'load_web': '手动加载网页档案…', 'reset': '停用九点修正', 'record': '手动录制',
        'clicks': '点击采集', 'click_session': '开始点击采集会话', 'abort_click_session': '中止点击会话',
        'autocal': '点击自动粗校准',
        'demo': '眼控 Demo…', 'presentation': '演示模式',
        'heatmap_toggle':'热力图累加开/关', 'heatmap_view':'查看热力图', 'heatmap_clear':'清空热力图', 'heatmap_export':'导出当前会话热力图',
        'toggle': '隐藏悬浮层', 'pause': '暂停摄像头', 'web': '打开网页', 'quit': '退出',
    }

    def __init__(self, parent=None):
        self.items = {key: QAction(label, parent) for key, label in self.LABELS.items()}
        self.items['record'].setCheckable(True)
        self.items['clicks'].setCheckable(True)
        self.items['autocal'].setCheckable(True)
        self.items['heatmap_toggle'].setCheckable(True)
        self.items['heatmap_view'].setCheckable(True)
        self.items['presentation'].setCheckable(True)

    def __getitem__(self, name):
        return self.items[name]

    def connect(self, name, function):
        self[name].triggered.connect(function)

    def add_to_menu(self, menu):
        for name, action in self.items.items():
            if name in {'record', 'toggle', 'quit'}: menu.addSeparator()
            menu.addAction(action)


class TrayFallback:
    """A finite startup check; geometry cannot prove visibility on a notched Mac."""
    def __init__(self):
        self.notified = False

    def check(self, available, visible, height, *, final=False):
        if self.notified or (available and not final): return False
        missing = not available or not visible or height <= 0
        if missing: self.notified = True
        return missing


def panel_at_start(policy, platform, *, forced=False):
    return forced or policy == 'always' or (policy == 'auto' and platform == 'darwin')


def panel_on_tray_failure(policy):
    return policy != 'never'
