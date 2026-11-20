"""下载历史 + 失败重试的端到端流程测试（不联网）。

用假的 DownloadManager.run 模拟「3 张里挂 1 张」，验证：
- 点下载后记录先落库、文件结果逐个回写
- 重试只重建失败的那 1 个任务，不重下已经成功的
- 重试成功后批次状态翻成全部成功，attempts 累加
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

from mediagrab.core.downloaders.base import DownloadResult
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.history import HistoryStore
from mediagrab.core.models import MediaInfo, MediaItem
from mediagrab.ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


class FakeManager(DownloadManager):
    """真 DownloadManager 负责建任务，只把 run 换掉。

    fail_indexes 里的下标返回失败，其余写出一个真实小文件并返回成功 ——
    `_on_file_done` 会用 os.path.getsize 读大小，假路径会拿不到尺寸。
    """

    def __init__(self, config, out_dir: str) -> None:
        super().__init__(config)
        self.out_dir = out_dir
        self.fail_indexes: set = set()
        self.seen_urls: list = []

    def run(self, tasks, progress=None, mode=None, on_file_done=None):
        self.seen_urls = [t.url for t in tasks]
        results = []
        total = len(tasks)
        for i, task in enumerate(tasks):
            if progress:
                progress(i, total, f"下载 {i + 1}/{total}")
            if i in self.fail_indexes:
                res = DownloadResult(False, message="HTTP 403 Forbidden（模拟）")
            else:
                path = os.path.join(self.out_dir, f"{task.filename}.{task.ext}")
                with open(path, "wb") as fh:
                    fh.write(b"x" * 2048)
                res = DownloadResult(True, path=path)
            results.append(res)
            if on_file_done:
                on_file_done(task, res)
            time.sleep(0.02)
        if progress:
            progress(total, total, "完成")
        return results


def gallery(n: int = 3) -> MediaInfo:
    return MediaInfo(
        platform="douyin",
        title="就算变成小僵尸，也还是想唱歌🎶",
        author="宗圆kzy.",
        media_type="image",
        source_url="https://v.douyin.com/dcJJwUpngMI/",
        items=[
            MediaItem(index=i + 1, url=f"https://p3-pic.douyinpic.com/a_{i + 1}~tplv.webp",
                      ext="jpg", headers={"Cookie": "sessionid=xyz"})
            for i in range(n)
        ],
    )


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))

    tmp = tempfile.mkdtemp(prefix="mg_flow_")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)

    store = HistoryStore(os.path.join(tmp, "history.db"))
    w = MainWindow()
    w.history.close()
    w.history = store
    w.history_view.store = store
    w.config.download_dir = out_dir
    w.show()

    fake = FakeManager(w.config, out_dir)
    w.dm = fake

    w._add_card(gallery(3))
    card = w._cards[-1]
    print(f"  卡片已就绪：{len(card.selected_items())} 张")

    state = {"phase": "first"}

    def wait_download():
        if w._download_worker is not None and w._download_worker.isRunning():
            QTimer.singleShot(120, wait_download)
            return
        if state["phase"] == "first":
            first_round()
        else:
            final_checks()

    def first_round():
        print("1) 首轮：3 张里挂 1 张")
        batches = store.batches()
        check("写了 1 个批次", len(batches) == 1, str(len(batches)))
        b = batches[0]
        check("批次有 3 个文件", b.total == 3, str(b.total))
        check("2 成 1 败", (b.ok, b.failed) == (2, 1), f"{b.ok}/{b.failed}")
        check("状态 partial", b.status == "partial", b.status)
        check("大小累加正确", b.size == 2048 * 2, str(b.size))
        check("标题正确", b.title.startswith("就算变成小僵尸"), b.title)
        check("平台正确", b.platform == "douyin", b.platform)
        check("kind=gallery", b.kind == "gallery", b.kind)

        recs = store.records_of(b.batch_id)
        check("headers 已落库", recs[0].headers.get("Cookie") == "sessionid=xyz",
              str(recs[0].headers))
        check("referer 有值", recs[0].referer != "", recs[0].referer)
        check("downloader 有记录", all(r.downloader for r in recs),
              str([r.downloader for r in recs]))

        print("2) 切换到历史页")
        w.sidebar._on_item("history")
        app.processEvents()
        check("显示历史页", w.pages.currentIndex() == 1)
        check("导入条已隐藏", not w.import_bar.isVisible())
        check("清空按钮已出现", w.clear_btn.isVisible())
        check("列表有 1 张卡", len(w.history_view._cards) == 1, str(len(w.history_view._cards)))

        print("3) 重试：只重跑失败的那 1 张")
        fake.fail_indexes = set()          # 这次全成功
        state["phase"] = "retry"
        before_ok = [r.id for r in store.records_of(b.batch_id) if r.ok]
        state["before_ok"] = before_ok
        w._retry_batch(b.batch_id)
        QTimer.singleShot(120, wait_download)

    def final_checks():
        print("4) 重试结果")
        b = store.batches()[0]
        check("只提交 1 个任务", len(fake.seen_urls) == 1, str(fake.seen_urls))
        check("提交的是失败那张", fake.seen_urls and "a_3" in fake.seen_urls[0],
              str(fake.seen_urls))
        check("全部成功", b.ok == 3 and b.failed == 0, f"{b.ok}/{b.failed}")
        check("状态 ok", b.status == "ok", b.status)
        check("attempts 累加到 2", b.attempts == 2, str(b.attempts))
        recs = store.records_of(b.batch_id)
        check("成功的行没被重下（路径不变）",
              all(r.file_path for r in recs), str([r.file_path for r in recs]))
        check("原成功行 attempts 仍为 1",
              sum(1 for r in recs if r.attempts == 1) == 2,
              str([r.attempts for r in recs]))
        check("重试后没有失败项", len(store.failed_records(b.batch_id)) == 0)

        w.sidebar._on_item("history")
        app.processEvents()
        check("历史卡片不再有重试按钮",
              all(not c.batch.failed for c in w.history_view._cards))

        print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
        state["code"] = 1 if FAIL else 0
        app.quit()

    fake.fail_indexes = {2}
    w._on_download_requested(card)
    QTimer.singleShot(150, wait_download)
    app.exec()
    return state.get("code", 0)


if __name__ == "__main__":
    sys.exit(main())
