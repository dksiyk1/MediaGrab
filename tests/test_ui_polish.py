"""UI 润色验证：卡片淡入、切页淡入、滚动条按需显示。

跑法：python tests/test_ui_polish.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.models import FormatOption, MediaInfo, MediaItem
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


def wait_effect_gone(app, widget, timeout=2.0):
    """等到 graphicsEffect 被摘掉。

    固定 pump(320) 在机器忙时会假失败：动画是 260ms，但事件循环被别的东西
    占住时推进不按墙钟走。轮询到真的没有 effect 为止。
    """
    end = time.time() + timeout
    while time.time() < end:
        if widget.graphicsEffect() is None:
            return True
        pump(app, 40)
    return widget.graphicsEffect() is None


def video() -> MediaInfo:
    return MediaInfo(
        platform="bilibili", title="测试视频", author="作者",
        formats=[FormatOption("a", "1080P"), FormatOption("b", "720P")],
    )


def gallery() -> MediaInfo:
    return MediaInfo(
        platform="douyin", title="测试图集", author="作者", media_type="image",
        items=[MediaItem(index=i + 1, url=f"a://{i}") for i in range(3)],
    )


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    win = MainWindow()
    win.show()
    pump(app, 300)

    print("1) 卡片入场动画")
    win._add_card(video())
    card = win._cards[-1]
    check("插入后立即有透明度效果", card.graphicsEffect() is not None)
    check("动画结束后摘掉 effect（避免离屏渲染）", wait_effect_gone(app, card))

    print("2) 多张卡片各自淡入")
    win._add_card(gallery())
    c2 = win._cards[-1]
    check("第二张也在动画中", c2.graphicsEffect() is not None)
    check("第二张也收尾", wait_effect_gone(app, c2))

    print("3) 切页淡入")
    win.sidebar._on_item("history")
    pump(app, 60)
    check("历史页有淡入效果", win.pages.currentWidget().graphicsEffect() is not None)
    pump(app, 260)
    check("切页动画收尾", win.pages.currentWidget().graphicsEffect() is None)

    print("4) 滚动条按需显示")
    check("历史滚动条 AsNeeded",
          win.history_view.scroll.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded,
          str(win.history_view.scroll.verticalScrollBarPolicy()))
    check("队列滚动条 AsNeeded",
          win.queue_view.scroll.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded,
          str(win.queue_view.scroll.verticalScrollBarPolicy()))

    win.close()
    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
