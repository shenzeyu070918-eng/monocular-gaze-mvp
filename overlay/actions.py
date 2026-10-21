"""One set of Qt actions shared by menus and the ordinary control panel."""
from PySide6.QtGui import QAction


class OverlayActions:
    LABELS = {
        'panel': '打开控制面板', 'calibrate': '开始九点校准', 'check': '精度核对（A/B）',
        'load_web': '手动加载网页档案…', 'reset': '停用个人修正', 'record': '手动录制',
        'toggle': '隐藏悬浮层', 'pause': '暂停摄像头', 'web': '打开网页', 'quit': '退出',
    }

    def __init__(self, parent=None):
        self.items = {key: QAction(label, parent) for key, label in self.LABELS.items()}
        self.items['record'].setCheckable(True)

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
