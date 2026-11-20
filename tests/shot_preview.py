"""预览浮层的渲染检查：造一张带真实图片的卡片，打开预览并截图。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mediagrab.core.models import FormatOption, MediaInfo, MediaItem  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMGDIR = os.path.join(ROOT, "_e2e_out", "note_full")


def load_photos():
    out = []
    if not os.path.isdir(IMGDIR):
        return out
    for name in sorted(os.listdir(IMGDIR)):
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            with open(os.path.join(IMGDIR, name), "rb") as fh:
                out.append(fh.read())
    return out


def fake_video() -> MediaInfo:
    return MediaInfo(
        platform="bilibili",
        title="旗舰手机芯片大横评：苹果高通发哥玄戒谁最强？",
        author="极客湾Geekerwan",
        duration=3434,
        source_url="https://www.bilibili.com/video/BV1kMxM6FECM",
        formats=[
            FormatOption("30080+30280", "1080P · MP4 · H.264 · 313.1MB", filesize=328_300_000),
            FormatOption("30280", "音频 170kbps · 70.0MB", filesize=73_400_000, kind="audio"),
        ],
    )


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))
    from mediagrab.ui.main_window import MainWindow

    photos = load_photos()
    w = MainWindow()
    w.resize(940, 780)
    w._add_card(fake_video())

    items = [MediaItem(index=i + 1, url=f"https://example.com/{i}.jpg", ext="jpg")
             for i in range(max(3, len(photos)))]
    info = MediaInfo(
        platform="douyin",
        title="就算变成小僵尸，也还是想唱歌🎶。📷：自己 出镜：鲨鲨 #cos #摄影 #初音未来 #二次元 #cosplay",
        author="宗圆kzy.",
        source_url="https://v.douyin.com/dcJJwUpngMI/",
        media_type="image",
        referer="https://www.douyin.com/",
        items=items,
    )
    w._add_card(info)
    card = w._cards[-1]
    # 走真实回调，顺便验证封面兜底（适配器没给独立封面时用首图顶）
    for i, data in enumerate(photos):
        if i < len(items):
            card.set_gallery_image(items[i].url, data)
    w.status.setText(f"解析成功 · {len(photos)} 张图片")
    w.progress.setValue(100)
    w.show()
    app.processEvents()
    app.processEvents()
    app.processEvents()

    out1 = os.path.join(ROOT, "_ui_shot_cards.png")
    w.grab().save(out1)
    print("卡片态:", out1, os.path.getsize(out1))

    # 打开预览
    w._on_preview_requested(card, 1)
    for _ in range(16):
        app.processEvents()
        if w.preview._fade > 0.99:
            break
        QApplication.instance().thread().msleep(25)
    app.processEvents()

    out2 = os.path.join(ROOT, "_ui_shot_preview.png")
    w.grab().save(out2)
    print("预览态:", out2, os.path.getsize(out2))
    print("可见:", w.preview.isVisible(), " fade=%.2f" % w.preview._fade,
          " 当前=%d/%d" % (w.preview._index + 1, len(w.preview._items)))

    # 切到下一张再拍
    w.preview.step(1)
    app.processEvents()
    out3 = os.path.join(ROOT, "_ui_shot_preview2.png")
    w.grab().save(out3)
    print("预览态2:", out3, os.path.getsize(out3))

    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
