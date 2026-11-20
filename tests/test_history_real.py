"""真实下载一次，验证历史记录写的是真实文件路径与大小（需要联网）。

使用抖音图文那条：3 张原图，不涉及 ffmpeg 合并，失败面最小。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.history import HistoryStore, human_size
from mediagrab.ui.main_window import MainWindow

URL = "https://v.douyin.com/dcJJwUpngMI/"
PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))

    tmp = tempfile.mkdtemp(prefix="mg_real_")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)

    store = HistoryStore(os.path.join(tmp, "history.db"))
    w = MainWindow()
    w.history.close()
    w.history = store
    w.history_view.store = store
    w.config.download_dir = out_dir

    info = w.engine.parse(URL)
    w._add_card(info)
    card = w._cards[-1]
    print(f"  解析到「{info.title[:20]}」{len(info.items)} 张")

    state = {"code": 0}

    def wait_done():
        if w._download_worker is not None and w._download_worker.isRunning():
            QTimer.singleShot(200, wait_done)
            return

        batches = store.batches()
        check("写了 1 个批次", len(batches) == 1, str(len(batches)))
        if not batches:
            state["code"] = 1
            app.quit()
            return
        b = batches[0]
        print(f"  批次：{b.ok}/{b.total} 成功，{human_size(b.size)}，attempts={b.attempts}")

        check("文件数与解析一致", b.total == len(info.items), f"{b.total} vs {len(info.items)}")
        check("全部成功", b.ok == b.total and b.failed == 0, f"{b.ok}/{b.total}")
        check("有真实大小", b.size > 100_000, str(b.size))

        recs = store.records_of(b.batch_id)
        check("路径都真实存在", all(os.path.isfile(r.file_path) for r in recs),
              str([r.file_path for r in recs]))
        check("落盘大小与记录一致",
              all(os.path.getsize(r.file_path) == r.file_size for r in recs),
              str([(os.path.getsize(r.file_path), r.file_size) for r in recs]))
        check("重试参数齐全（url/headers/referer）",
              all(r.url and r.referer for r in recs), "缺字段")
        check("图片任务带 Cookie（CDN 防盗链）",
              all(r.headers.get("Cookie") for r in recs), str(recs[0].headers.keys()))
        check("downloader 记录为 internal", recs[0].downloader == "internal",
              recs[0].downloader)
        check("标题与作者正确",
              recs[0].title.startswith("就算变成小僵尸") and recs[0].author == "宗圆kzy.",
              f"{recs[0].title[:12]} / {recs[0].author}")

        print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
        state["code"] = 1 if FAIL else 0
        app.quit()

    w._on_download_requested(card)
    QTimer.singleShot(300, wait_done)
    app.exec()
    return state["code"]


if __name__ == "__main__":
    sys.exit(main())
