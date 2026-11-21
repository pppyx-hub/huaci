# -*- coding: utf-8 -*-
"""
划词翻译助手 · 词库界面（Qt + WebEngine）
==========================================
独立进程运行，与 tkinter 划词程序共用同一个 vocab.db（WAL 模式）。

界面全部用 HTML/CSS 渲染，所以圆角、阴影、字体抗锯齿都是浏览器级的。
"""

import os
import sys

# WebEngine 在无 GPU 环境下必须单进程 + 关沙箱，否则渲染不出来
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --disable-gpu --single-process --disable-dev-shm-usage "
    "--disable-features=Translate")
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from PySide6.QtCore import Qt, QUrl, QTimer          # noqa: E402
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView    # noqa: E402
from PySide6.QtWebChannel import QWebChannel             # noqa: E402

import bridge  # noqa: E402
import vault   # noqa: E402
import threading  # noqa: E402


def _icon():
    """程序图标：深底 + 蓝字。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1d4ed8"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 16, 16)
    p.setPen(QColor("#ffffff"))
    f = QFont("Microsoft YaHei UI", 26, QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "译")
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("划词翻译助手")
        self.setWindowIcon(_icon())
        self.resize(1180, 760)
        self.setMinimumSize(960, 620)
        self.view = None
        self.server = None
        self.port = 0

        # 先把窗口摆出来。WebEngine 初始化要 2–3 秒，用户看不到窗口
        # 就以为没反应，会反复点悬浮球（这是「要点好几下」的原因）。
        from PySide6.QtWidgets import QLabel
        from PySide6.QtCore import Qt as _Qt, QTimer as _T
        self._splash = QLabel("正在加载词库…")
        self._splash.setAlignment(_Qt.AlignCenter)
        self._splash.setStyleSheet(
            "background:#0f1012;color:#7a7f88;font-size:14px;letter-spacing:.5px")
        self.setCentralWidget(self._splash)

        # 深色标题栏（尽早设，窗口一出现就是深色）
        try:
            import ctypes
            hwnd = int(self.winId())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 20, ctypes.byref(v), ctypes.sizeof(v))
            dark = ctypes.c_int(0x00000000)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 35, ctypes.byref(dark), ctypes.sizeof(dark))
        except Exception:
            pass

        # 等窗口真正显示出来后再造 WebEngine
        _T.singleShot(30, self._build_web)

    def _build_web(self):
        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)

        # 渲染优化：软件渲染下这两项能减少重绘开销（属性名拼错会直接崩，务必 try）
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            st = self.view.settings()
            st.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
            st.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, False)
        except Exception as e:
            print("  [WebEngine] 渲染设置跳过:", e)

        # 起本地 HTTP 服务（随机端口），前端用 fetch 调，比 WebChannel 稳
        from werkzeug.serving import make_server
        self.server = make_server("127.0.0.1", 0, bridge.create_server(),
                                  threaded=True)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        self.view.load(QUrl("http://127.0.0.1:%d/" % self.port))

    def closeEvent(self, e):
        try:
            if self.server:
                self.server.shutdown()
        except Exception:
            pass
        super().closeEvent(e)


MUTEX_NAME = r"Local\HuaciVaultUI_SingleInstance"


def _already_running():
    """是否已有实例。CreateMutexW 必须声明 argtypes，
    否则字符串指针被截断、创建失败（ERROR_INVALID_NAME=123），
    检测永远失效 → 每点一次就多开一个进程。"""
    import ctypes
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                   ctypes.c_wchar_p]
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        return err == 183          # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def _focus_existing():
    """已有实例时把它切到前台，别再开新的。"""
    import ctypes
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.FindWindowW.restype = ctypes.c_void_p
        hwnd = u.FindWindowW(None, "划词翻译助手")
        if hwnd:
            u.ShowWindow(ctypes.c_void_p(hwnd), 9)        # SW_RESTORE
            u.SetForegroundWindow(ctypes.c_void_p(hwnd))
            return True
    except Exception:
        pass
    return False


def main():
    if _already_running():
        _focus_existing()
        return
    vault.init()
    app = QApplication(sys.argv)
    app.setApplicationName("划词翻译助手")
    app.setStyle("Fusion")

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
