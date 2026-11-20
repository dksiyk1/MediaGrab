"""渲染队列页与分P入口，用于视觉验收。不联网。"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.downloaders.base import DownloadTask
from mediagrab.core.history import HistoryStore
from mediagrab.core.models import FormatOption, MediaInfo, MediaItem
from mediagrab.ui.main_window import MainWindow
from mediagrab.ui.queue import QueueJob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_ui_shots")

PAGES = [
    {"index": 1, "part": "有关星空的寓言集·其一", "duration": 214, "cid": 1},
    {"index": 2, "part": "有关星空的寓言集·其二", "duration": 198, "cid": 2},
    {"index": 3, "part": "有关星空的寓言集·其三", "duration": 226, "cid": 3},
    {"index": 4, "part": "幕后花絮：配音现场", "duration": 132, "cid": 4},
]


def multip_info() -> MediaInfo:
    fmts = [
        FormatOption(format_id="30080", label="1080P · MP4 · 需 ffmpeg 合并", resolution="1080P"),
        FormatOption(format_id="64", label="720P · MP4 · 45MB", resolution="720P"),
        FormatOption(format_id="32", label="480P · MP4 · 18MB", resolution="480P"),
    ]
    return MediaInfo(
        platform="bilibili",
        title="《崩坏：星穹铁道》千星纪游PV：「有关星空的寓言集•其三」",
        author="崩坏星穹铁道",
        duration=226,
        formats=fmts,
        source_url="https://www.bilibili.com/video/BV1JYsd6sE4k",
        referer="https://www.bilibili.com/",
        extra={"pages": 4, "page_list": PAGES},
        adapter="bilibili",
    )


def gallery_info() -> MediaInfo:
    return MediaInfo(
        platform="douyin",
        title="就算变成小僵尸，也还是想唱歌🎶 #cos #初音未来",
        author="宗圆kzy.",
        media_type="image",
        source_url="https://v.douyin.com/dcJJwUpngMI/",
        items=[MediaItem(index=i + 1, url=f"https://p3-pic.douyinpic.com/x{i}", ext="jpg")
               for i in range(3)],
    )


def job(title: str, state: str, total: int, done: int = 0, ok: int = 0,
        failed: int = 0, platform: str = "bilibili", kind: str = "video",
        message: str = "") -> QueueJob:
    return QueueJob(
        job_id=title[:4] + state,
        batch_id="b" + title[:3],
        title=title,
        platform=platform,
        kind=kind,
        mode="internal",
        tasks=[DownloadTask(url="x", output_dir=".", filename=f"f{i}") for i in range(total)],
        state=state,
        done=done,
        ok=ok,
        failed=failed,
        message=message,
        sample_path=os.path.join(os.path.expanduser("~"), "Downloads", "MediaGrab"),
    )


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))

    tmp = tempfile.mkdtemp(prefix="mg_shot_q_")
    store = HistoryStore(os.path.join(tmp, "history.db"))

    w = MainWindow()
    w.history.close()
    w.history = store
    w.history_view.store = store
    w.resize(1200, 800)
    w.show()

    w._add_card(multip_info())
    w._add_card(gallery_info())

    w._queue = [
        job("《崩坏：星穹铁道》千星纪游PV：「有关星空的寓言集•其三」_P3_有关星空的寓言集·其三",
            "running", 1, done=0),
        job("【2026全球总决赛】10月29日 LYON vs IG_P2_第二局", "waiting", 1,
            platform="bilibili"),
        job("【4K】上海夜景航拍合集", "failed", 1, done=1, ok=0, failed=1,
            message="ffmpeg 未找到，无法合并音视频流"),
        job("就算变成小僵尸，也还是想唱歌🎶 #cos #初音未来", "done", 3, done=3, ok=3,
            platform="douyin", kind="gallery"),
    ]
    w._refresh_counts()

    def shoot():
        w.grab().save(os.path.join(OUT, "10-queue.png"))
        print("saved 10-queue.png")
        # 主页面：看分P 入口
        w.sidebar._on_item("all")
        for _ in range(4):
            app.processEvents()
        QTimer.singleShot(400, lambda: (w.grab().save(os.path.join(OUT, "11-multip.png")),
                                        print("saved 11-multip.png"), app.quit()))

    QTimer.singleShot(500, lambda: (w.sidebar._on_item("queue"),
                                    QTimer.singleShot(500, shoot)))
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
