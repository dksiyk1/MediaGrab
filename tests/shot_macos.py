"""渲染 macOS 版主窗口并截图，用于视觉验收。

不联网：用真实图片文件构造 MediaInfo，直接塞进卡片。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.models import FormatOption, MediaInfo, MediaItem
from mediagrab.ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = [os.path.join(ROOT, "_e2e_out", "note_full", f)
          for f in os.listdir(os.path.join(ROOT, "_e2e_out", "note_full"))
          if f.endswith(".jpg")] if os.path.isdir(os.path.join(ROOT, "_e2e_out", "note_full")) else []


def gallery_info() -> MediaInfo:
    items = [MediaItem(index=i + 1, url=f"local://{i + 1}", ext="jpg") for i in range(3)]
    return MediaInfo(
        platform="douyin",
        title="就算变成小僵尸，也还是想唱歌🎶。📷：自己 出镜：鲨鲨 #cos #摄影 #初音未来 #二次元 #cosplay",
        author="宗圆kzy.",
        items=items,
        media_type="image",
        source_url="https://v.douyin.com/dcJJwUpngMI/",
    )


def video_three() -> MediaInfo:
    fmts = [
        FormatOption(format_id="30080", label="1080P", resolution="1080P"),
        FormatOption(format_id="64", label="720P", resolution="720P"),
        FormatOption(format_id="32", label="576P", resolution="576P"),
    ]
    return MediaInfo(
        platform="douyin",
        title="我这样的人 我什么样 我又这样 我又哪样 #少女感 #女大学生",
        author="y28.",
        duration=11,
        formats=fmts,
        source_url="https://v.douyin.com/Fx0jhBZycbM/",
    )


def video_many() -> MediaInfo:
    fmts = [
        FormatOption(format_id="127", label="8K · 超清", resolution="8K"),
        FormatOption(format_id="120", label="4K · 超清", resolution="4K"),
        FormatOption(format_id="116", label="1080P60", resolution="1080P60"),
        FormatOption(format_id="112", label="1080P+", resolution="1080P+"),
        FormatOption(format_id="80", label="1080P", resolution="1080P"),
        FormatOption(format_id="64", label="720P", resolution="720P"),
    ]
    return MediaInfo(
        platform="bilibili",
        title="反诈博主「微剑」被曝 2 年前造买凶遇害",
        author="极客湾",
        duration=57 * 60 + 14,
        formats=fmts,
        source_url="https://www.bilibili.com/video/BV1pmxK6REXd",
        warn="未登录，只能获取较低清晰度；导入浏览器 Cookie 后可解锁 1080P",
    )


def main() -> None:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()

    for info in (gallery_info(), video_three(), video_many()):
        w._add_card(info)

    cards = w._cards
    gallery = cards[0]
    # 塞真实图片：封面 + 图集
    datas = []
    for p in PHOTOS[:3]:
        with open(p, "rb") as fh:
            datas.append(fh.read())
    for i, data in enumerate(datas):
        gallery.set_gallery_image(f"local://{i + 1}", data)
    if datas:
        gallery.set_cover(datas[0])

    w.search.setText("")
    app.processEvents()
    for _ in range(6):
        app.processEvents()

    print("window logical size:", w.width(), "x", w.height())
    out = os.path.join(ROOT, "_ui_macos_main.png")
    out_prev = os.path.join(ROOT, "_ui_macos_preview.png")

    def shoot():
        w.grab().save(out)
        # 打开图片预览，确认浮层在新结构下仍然正常
        w._on_preview_requested(gallery, 1)
        for _ in range(8):
            app.processEvents()
        QTimer.singleShot(500, lambda: (w.grab().save(out_prev), app.quit()))

    QTimer.singleShot(600, shoot)
    app.exec()
    for f in (out, out_prev):
        print("saved", os.path.basename(f), os.path.getsize(f) // 1024, "KB")


if __name__ == "__main__":
    main()
