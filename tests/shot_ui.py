"""渲染主窗口并截图，用于检查视觉样式。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mediagrab.core.models import FormatOption, MediaInfo, MediaItem  # noqa: E402


def fake_video() -> MediaInfo:
    return MediaInfo(
        platform="bilibili",
        title="旗舰手机芯片大横评：苹果高通发哥玄戒谁最强？",
        author="极客湾Geekerwan",
        duration=3434,
        source_url="https://www.bilibili.com/video/BV1kMxM6FECM",
        formats=[
            FormatOption("30080+30280", "1080P · MP4 · H.264 · 313.1MB", filesize=328_300_000),
            FormatOption("30064+30280", "720P · MP4 · H.264 · 188.4MB", filesize=197_500_000),
            FormatOption("30280", "音频 170kbps · 70.0MB", filesize=73_400_000, kind="audio"),
        ],
    )


def fake_gallery() -> MediaInfo:
    items = [MediaItem(index=i + 1, url=f"https://example.com/{i}.jpg") for i in range(7)]
    return MediaInfo(
        platform="douyin",
        title="🍈～ #cos #橘望",
        author="葡萄汽水喵",
        source_url="https://v.douyin.com/oI4Hr1AEFuk/",
        media_type="image",
        items=items,
    )


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))
    from mediagrab.ui.main_window import MainWindow

    w = MainWindow()
    w.resize(920, 780)
    w.input.setPlainText(
        "5.64 :3pm U@y.Gi 04/28 eBt:/ 🍈～ # cos #橘望  https://v.douyin.com/oI4Hr1AEFuk/ 复制此链接，打开Dou音搜索"
    )
    w._add_card(fake_video())
    w._add_card(fake_gallery())
    w.status.setText("解析成功 · 3 个清晰度")
    w.progress.setValue(38)
    w.show()
    app.processEvents()
    app.processEvents()

    out = os.path.join(ROOT, "_ui_shot.png")
    w.grab().save(out)
    print("已保存:", out, os.path.getsize(out), "字节")
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
