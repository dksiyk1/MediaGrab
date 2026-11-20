"""解析中取消：按钮变「取消」、点了能立刻停下、不留错误块。

跑法：python tests/test_parse_cancel.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mediagrab.ui.main_window import MainWindow

URL = "https://v.douyin.com/Fx0jhBZycbM/"     # 完整解析要 ~29s，够中途取消

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
    win = MainWindow()
    win.show()
    pump(app, 300)

    check("初始按钮是「解析」", win.parse_btn.text() == "解析", win.parse_btn.text())

    win.input.setPlainText(URL)
    win._start_parse()
    worker = win._parse_worker
    pump(app, 200)

    check("解析已启动", worker is not None and worker.isRunning())
    check("按钮变成「取消」", win.parse_btn.text() == "取消", win.parse_btn.text())
    check("按钮仍可点（不是禁用干等）", win.parse_btn.isEnabled())

    # 等它跑进耗时阶段，再点取消
    pump(app, 1500)
    stage = win.engine.ctx.stage
    print(f"       当前阶段：{stage or '(未设置)'}  状态栏：{win.status.text()!r}")

    t0 = time.time()
    QTest.mouseClick(win.parse_btn, Qt.LeftButton)
    pump(app, 80)
    check("点击后先显示正在取消", "取消" in win.status.text(), win.status.text())

    while worker.isRunning() and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.02)
    elapsed = time.time() - t0

    check("worker 已停止", not worker.isRunning())
    check("取消在 6 秒内生效", elapsed < 6, f"{elapsed:.2f}s")
    pump(app, 300)
    check("状态栏显示已取消", "已取消" in win.status.text(), win.status.text())
    check("按钮恢复「解析」", win.parse_btn.text() == "解析", win.parse_btn.text())
    check("没有插入错误块（错误块里含「解析失败：」）",
          "解析失败：" not in " ".join(
              l.text() for l in win.findChildren(type(win.empty_tip)) if hasattr(l, "text")))
    check("取消标志已置位", win.engine.ctx.cancel.is_set())

    # 取消后再解析，必须能正常重新开始（标志被 reset）
    win._start_parse()
    pump(app, 200)
    check("取消后能再次开始解析", win._parse_worker is not worker and win._parse_worker.isRunning())
    check("重新开始时取消标志已清", not win.engine.ctx.cancel.is_set())
    win._cancel_parse()
    pump(app, 1500)

    win.close()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
