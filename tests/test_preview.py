"""预览交互测试：双击、角标、方向键、ESC、点击空白关闭。

不需要真实网络 —— 直接把字节塞进缩略图。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mediagrab.core.models import MediaInfo, MediaItem  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMGDIR = os.path.join(ROOT, "_e2e_out", "note_full")

FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global FAIL
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + extra) if extra else ''}")
    if not cond:
        FAIL += 1


def pump(app, ms: int = 260) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.008)


def main() -> int:
    photos = []
    if os.path.isdir(IMGDIR):
        for name in sorted(os.listdir(IMGDIR)):
            if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                with open(os.path.join(IMGDIR, name), "rb") as fh:
                    photos.append(fh.read())
    if len(photos) < 2:
        print("跳过：_e2e_out/note_full 里没有足够的测试图片")
        return 1

    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))
    from mediagrab.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(920, 760)
    items = [MediaItem(index=i + 1, url=f"https://example.com/{i}.jpg") for i in range(len(photos))]
    info = MediaInfo(platform="douyin", title="测试作品标题 #cos",
                     author="tester", media_type="image", items=items,
                     referer="https://www.douyin.com/")
    w._add_card(info)
    card = w._cards[-1]
    for i, data in enumerate(photos):
        card.set_gallery_image(items[i].url, data)
    w.show()
    pump(app, 200)

    thumbs = card._thumbs
    prev = w.preview

    print("1) 初始状态")
    check("预览未显示", not prev.isVisible())
    check("缩略图已缓存字节", all(t.raw for t in thumbs))
    check("封面已用首图兜底", card._cover_done)

    print("2) 双击第 1 张 → 打开预览")
    before = [t.checked for t in thumbs]
    QTest.mouseDClick(thumbs[0], Qt.LeftButton, pos=QPoint(40, 40))
    pump(app, 320)
    check("预览已显示", prev.isVisible())
    check("索引=0", prev._index == 0, f"实际 {prev._index}")
    check("淡入完成", prev._fade > 0.95, f"fade={prev._fade:.2f}")
    check("双击不改变选中状态", [t.checked for t in thumbs] == before)

    print("3) 方向键 / 滚轮切换")
    QTest.keyClick(prev, Qt.Key_Right)
    pump(app, 60)
    check("→ 切到第 2 张", prev._index == 1, f"实际 {prev._index}")
    QTest.keyClick(prev, Qt.Key_Left)
    pump(app, 60)
    check("← 回到第 1 张", prev._index == 0, f"实际 {prev._index}")
    QTest.keyClick(prev, Qt.Key_Left)
    pump(app, 60)
    check("← 在首张循环到末张", prev._index == len(photos) - 1, f"实际 {prev._index}")

    print("4) 点击空白关闭")
    QTest.keyClick(prev, Qt.Key_Right)
    pump(app, 60)
    QTest.mouseClick(prev, Qt.LeftButton, pos=QPoint(8, prev.height() - 8))
    pump(app, 340)
    check("点空白后关闭", not prev.isVisible())

    print("5) 放大角标点击 → 预览且不改选中")
    before = [t.checked for t in thumbs]
    # 放大热区在左下角（_in_zoom_badge），右上角那个圆点是选中勾，不可点
    d = thumbs[1].BADGE_HIT
    badge = QPoint(2 + d // 2, thumbs[1].height() - 2 - d // 2)
    QTest.mouseClick(thumbs[1], Qt.LeftButton, pos=badge)
    pump(app, 320)
    check("预览已打开", prev.isVisible())
    check("索引=1", prev._index == 1, f"实际 {prev._index}")
    check("角标点击不改变选中状态", [t.checked for t in thumbs] == before)

    print("6) ESC 关闭")
    QTest.keyClick(prev, Qt.Key_Escape)
    pump(app, 340)
    check("ESC 后关闭", not prev.isVisible())
    check("关闭后清空数据", not prev._items)

    print("7) 点图片内部不关闭")
    QTest.mouseDClick(thumbs[0], Qt.LeftButton, pos=QPoint(40, 40))
    pump(app, 320)
    center = prev._image_rect.center()
    QTest.mouseClick(prev, Qt.LeftButton, pos=center)
    pump(app, 200)
    check("点图片本体保持打开", prev.isVisible())

    print("8) 真实双击序列（Press + DblClick）也不改选中")
    prev.dismiss()
    pump(app, 340)
    before = [t.checked for t in thumbs]
    QTest.mousePress(thumbs[2], Qt.LeftButton, pos=QPoint(40, 40))
    QTest.mouseDClick(thumbs[2], Qt.LeftButton, pos=QPoint(40, 40))
    pump(app, 320)
    check("预览已打开", prev.isVisible())
    check("索引=2", prev._index == 2, f"实际 {prev._index}")
    check("选中状态未变", [t.checked for t in thumbs] == before,
          f"{before} -> {[t.checked for t in thumbs]}")

    print(f"\n{'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}")
    app.quit()
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
