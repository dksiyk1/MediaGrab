"""渲染下载历史视图并截图，用于视觉验收。

不联网：往临时库里造几个批次（全成功 / 有失败 / 纯失败 / 进行中），
覆盖历史卡片的全部状态分支。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.history import HistoryStore
from mediagrab.ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_ui_shots")


def seed(store: HistoryStore) -> None:
    """造四个批次，覆盖全部状态。"""
    now = int(time.time())

    # 1) 图集全成功
    b1 = store.begin_batch([
        dict(
            source_url="https://v.douyin.com/dcJJwUpngMI/", platform="douyin",
            title="就算变成小僵尸，也还是想唱歌🎶。📷：自己 出镜：鲨鲨 #cos #摄影 #初音未来",
            author="宗圆kzy.", media_type="image", kind="gallery", format_label="3 张图片",
            url=f"https://p3-pic.douyinpic.com/x{i}~tplv.webp", output_dir=r"C:\out",
            filename=f"僵尸初音_{i:02d}", ext="jpg", referer="https://www.douyin.com/",
        )
        for i in range(1, 4)
    ])
    for rid, size in zip(store.batch_ids_after_insert(b1), (233_000, 511_000, 498_000)):
        store.mark(rid, True, rf"C:\out\僵尸初音_0{size % 9}.jpg", size, "", "internal")

    # 2) 图集部分失败（20 张里挂 2 张）
    b2 = store.begin_batch([
        dict(
            source_url="https://www.xiaohongshu.com/explore/68f2a1", platform="xiaohongshu",
            title="秋日穿搭合集 | 三套通勤look 附链接 #ootd #秋季穿搭 #通勤",
            author="阿鹿的衣橱", media_type="image", kind="gallery", format_label="20 张图片",
            url=f"https://sns-img.xhscdn.com/{i}", output_dir=r"C:\out",
            filename=f"秋日穿搭_{i:02d}", ext="jpg", referer="https://www.xiaohongshu.com/",
        )
        for i in range(1, 21)
    ])
    for idx, rid in enumerate(store.batch_ids_after_insert(b2)):
        if idx in (7, 13):
            store.mark(rid, False, "", 0, "HTTP 403 Forbidden：图片已过期，需重新解析", "internal")
        else:
            store.mark(rid, True, rf"C:\out\秋日穿搭_{idx:02d}.jpg", 420_000, "", "internal")

    # 3) 视频，直接失败（yt-dlp 合并失败）
    b3 = store.begin_batch([dict(
        source_url="https://www.bilibili.com/video/BV1pmxK6REXd", platform="bilibili",
        title="【4K】这可能是今年最好看的城市宣传片了｜上海夜景航拍合集",
        author="影像志", media_type="video", kind="video", format_label="4K · 需 ffmpeg 合并",
        url="https://api.bilibili.com/x/player/playurl", output_dir=r"C:\out",
        filename="上海夜景", ext="mp4", referer="https://www.bilibili.com/",
        format_selector="120+30280", merge=True,
    )])
    store.mark(store.batch_ids_after_insert(b3)[0], False, "", 0,
               "ffmpeg 未找到，无法合并音视频流", "ytdlp")

    # 4) 正在进行
    store.begin_batch([
        dict(
            source_url="https://v.douyin.com/oI4Hr1AEFuk/", platform="douyin",
            title="橘望 · 落日与海 #摄影 #风景",
            author="橘望", media_type="image", kind="gallery", format_label="4 张图片",
            url=f"https://p3-pic.douyinpic.com/o{i}", output_dir=r"C:\out",
            filename=f"橘望_{i:02d}", ext="jpg",
        )
        for i in range(1, 5)
    ])

    # 一条超长标题，验证不会硬裁在字符中间
    store.begin_batch([dict(
        source_url="https://www.bilibili.com/video/BV1long", platform="bilibili",
        title="【纪录片】跟着镜头走过三十七座城市的清晨与深夜｜第一季全十二集完整版合集（含幕后花絮与导演访谈）",
        author="城市观察局", media_type="video", kind="video", format_label="1080P",
        url="x", output_dir=r"C://out", filename="城市", ext="mp4",
    )])

    # 让时间错开，看起来像真的用了几天
    import sqlite3

    conn = sqlite3.connect(str(store.path))
    conn.execute("UPDATE records SET created_at=?, updated_at=? WHERE batch_id=?",
                 (now - 3 * 86400, now - 3 * 86400, b1))
    conn.execute("UPDATE records SET created_at=?, updated_at=? WHERE batch_id=?",
                 (now - 26 * 3600, now - 26 * 3600, b2))
    conn.execute("UPDATE records SET created_at=?, updated_at=? WHERE batch_id=?",
                 (now - 5 * 3600, now - 5 * 3600, b3))
    conn.commit()
    conn.close()


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))

    tmp = tempfile.mkdtemp(prefix="mg_shot_hist_")
    store = HistoryStore(os.path.join(tmp, "history.db"))
    seed(store)

    w = MainWindow()
    w.history.close()
    w.history = store
    w.history_view.store = store
    w.resize(1200, 800)
    w.show()
    w.sidebar._on_item("history")
    app.processEvents()

    def shoot():
        w.grab().save(os.path.join(OUT, "08-history.png"))
        print("saved 08-history.png")
        # 空态
        store.clear()
        w.sidebar._on_item("all")
        w.sidebar._on_item("history")
        for _ in range(4):
            app.processEvents()
        QTimer.singleShot(300, lambda: (w.grab().save(os.path.join(OUT, "09-history-empty.png")),
                                        print("saved 09-history-empty.png"), app.quit()))

    QTimer.singleShot(700, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
