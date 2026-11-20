"""托盘常驻：关窗口不等于停下载，从托盘退出才是真退。

跑法：python tests/test_tray.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from mediagrab.ui.main_window import MainWindow

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name} {extra}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def pump(app, ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    available = QSystemTrayIcon.isSystemTrayAvailable()
    win = MainWindow()
    win.show()
    pump(app, 300)

    if not available:
        print("  SKIP 本机没有系统托盘，跳过托盘行为测试")
        win.close()
        print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
        return 0

    print("1) 托盘初始化")
    check("创建了托盘图标", win._tray is not None)
    check("默认不常驻（设置里没勾）", not win.config.close_to_tray)
    check("默认隐藏图标", not win._tray.isVisible())

    print("2) 未开启时关闭照旧")
    check("_to_tray 不接管", win._to_tray() is False)
    check("窗口仍可见", win.isVisible())

    print("3) 开启后关闭 = 收进托盘")
    win.config.close_to_tray = True
    win._sync_tray()
    check("图标显示出来", win._tray.isVisible())
    check("_to_tray 接管", win._to_tray() is True)
    pump(app, 100)
    check("窗口已隐藏", not win.isVisible())
    check("程序不会因窗口隐藏而退出",
          app.quitOnLastWindowClosed() is False,
          str(app.quitOnLastWindowClosed()))

    print("4) 从托盘唤回")
    win._show_from_tray()
    pump(app, 200)
    check("窗口重新可见", win.isVisible())

    print("5) 关掉开关后图标隐藏")
    win.config.close_to_tray = False
    win._sync_tray()
    check("图标隐藏", not win._tray.isVisible())
    win.config.close_to_tray = True
    win._sync_tray()

    print("6) 托盘退出是真的退出")
    win._quit_from_tray()
    check("force_quit 已置位", win._force_quit)
    check("quitOnLastWindowClosed 恢复", app.quitOnLastWindowClosed() is True,
          str(app.quitOnLastWindowClosed()))
    pump(app, 600)
    check("窗口已关闭", not win.isVisible())
    check("线程已收尾", win._download_worker is None or not win._download_worker.isRunning())

    win.config.close_to_tray = False
    pump(app, 100)
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
