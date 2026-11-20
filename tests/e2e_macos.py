"""真实链接端到端验证：解析 → 卡片渲染 → 缩略图 → 截图。

联网跑一次，确认换成新 UI 后业务链路没断。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1pmxK6REXd"
OUT = sys.argv[2] if len(sys.argv) > 2 else "_ui_macos_e2e.png"


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()

    w.input.setPlainText(URL)
    app.processEvents()

    state = {"t0": __import__("time").time(), "phase": "parsing", "shots": 0}

    def tick():
        elapsed = __import__("time").time() - state["t0"]
        if state["phase"] == "parsing":
            if w._cards:
                state["phase"] = "thumbs"
                state["t_mid"] = elapsed
                print(f"  解析完成 {elapsed:.1f}s · {len(w._cards)} 张卡片")
                return
            if elapsed > 90:
                print("  解析超时")
                w.grab().save(os.path.join(ROOT, OUT))
                app.quit()
                return
            return

        # 等缩略图落位，再多给一点时间让 worker 全部结束
        if elapsed - state["t_mid"] > 6:
            card = w._cards[-1]
            info = card.info
            print(f"  平台 {info.platform} · {info.title[:34]}")
            if info.items:
                got = sum(1 for t in card.thumbs_ready()) if hasattr(card, "thumbs_ready") else 0
                print(f"  图集 {len(info.items)} 张 · 已显示 {got}")
            else:
                print(f"  清晰度 {', '.join(f.resolution for f in info.formats)}")
                sel = card.selected_format()
                print(f"  当前选中 {sel.label if sel else '无'}")
            w.grab().save(os.path.join(ROOT, OUT))
            print(f"  截图 {OUT}")
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(250)

    w._start_parse()
    print(f"  开始解析 {URL}")
    app.exec()


if __name__ == "__main__":
    main()
