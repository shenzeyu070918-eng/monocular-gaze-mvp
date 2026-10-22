"""Register only named system shortcuts; never inspect general key events."""
import sys

ACTION_LABELS = {'toggle':'显示/隐藏','pause':'暂停/恢复','calibrate':'开始九点','panel':'控制面板','heatmap_toggle':'热力图累加开/关','heatmap_view':'查看/隐藏热力图','demo':'眼控 Demo'}


def parse_shortcut(text):
    parts = [p.strip().upper() for p in text.split('+')]
    aliases = {'CONTROL':'CTRL','OPTION':'ALT','COMMAND':'META','CMD':'META','WIN':'META'}
    parts = [aliases.get(p,p) for p in parts]
    if len(parts)<2 or len(set(parts))!=len(parts) or len(parts[-1])!=1 or parts[-1] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789':
        raise ValueError('使用 Ctrl/Alt/Shift/Meta 加一个英文字母或数字')
    if not set(parts[:-1]) <= {'CTRL','ALT','SHIFT','META'}:
        raise ValueError('热键修饰键无效')
    return frozenset(parts[:-1]), parts[-1]


class Hotkeys:
    """A partial registration failure does not undo other useful shortcuts."""
    def __init__(self, bindings, dispatch, *, backend_factory=None):
        self.status = {}; self.backend = None; self._actions = {}; self._dispatch = dispatch
        if backend_factory is None:
            def backend_factory(callback):
                if sys.platform=='darwin':
                    from .hotkeys_macos import MacHotkeys
                    return MacHotkeys(callback)
                if sys.platform=='win32':
                    from .hotkeys_windows import WindowsHotkeys
                    return WindowsHotkeys(callback)
                raise RuntimeError('当前平台没有原生系统热键适配器')
        try:self.backend = backend_factory(self._activated)
        except Exception as exc:self._backend_error = str(exc)
        seen=set()
        for ident,(action,text) in enumerate(bindings.items(),1):
            if not text:
                self.status[action] = '已禁用'; continue
            try:
                shortcut=parse_shortcut(text)
                if shortcut in seen:raise ValueError('与另一项热键重复')
                seen.add(shortcut)
                if self.backend is None:raise RuntimeError(self._backend_error)
                self.backend.register(ident,*shortcut)
                self._actions[ident]=action
                self.status[action]='已注册 '+text
            except Exception as exc:self.status[action]='注册失败 '+text+'：'+str(exc)

    def _activated(self, ident):
        action=self._actions.get(ident)
        if action is not None:
            print('收到热键：'+ACTION_LABELS.get(action,action),flush=True)
            self._dispatch(action)

    def close(self):
        self._actions.clear()
        if self.backend is not None:
            backend,self.backend=self.backend,None
            backend.close()

    def text(self):
        return '\n'.join(ACTION_LABELS[k]+'：'+v for k,v in self.status.items())
