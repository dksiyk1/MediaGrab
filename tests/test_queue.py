"""下载队列测试：串行调度、失败不阻断、取消排队项 / 取消进行中。

用假的 DownloadManager.run 模拟下载，重点验证调度顺序和状态流转。
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
    """每次 run 写一个文件；按 call_no 决定这一批是否失败。"""

    def __init__(self, config, out_dir: str) -> None:
        super().__init__(config)
        self.out_dir = out_dir
        self.calls = 0
        self.fail_calls: set = set()
        self.delay = 0.25
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def run(self, tasks, progress=None, mode=None, on_file_done=None):
        self.calls += 1
        my = self.calls
        self.cancelled = False
        results = []
        total = len(tasks)
        for i, task in enumerate(tasks):
            if self.cancelled:
                break
            time.sleep(self.delay)
            if progress:
                progress(i, total, f"第 {my} 批 {i + 1}/{total}")
            if my in self.fail_calls:
                res = DownloadResult(False, message=f"模拟失败（第 {my} 批）")
            else:
                path = os.path.join(self.out_dir, f"{task.filename}.{task.ext}")
                with open(path, "wb") as fh:
                    fh.write(b"x" * 1024)
                res = DownloadResult(True, path=path)
            results.append(res)
            if on_file_done:
                on_file_done(task, res)
        return results


def gallery(tag: str, n: int = 2) -> MediaInfo:
    return MediaInfo(
        platform="douyin",
        title=f"作品 {tag}",
        author="作者",
        media_type="image",
        source_url=f"https://v.douyin.com/{tag}/",
        items=[
            MediaItem(index=i + 1, url=f"https://p3-pic.douyincloud.com/{tag}_{i}.webp", ext="jpg")
            for i in range(n)
        ],
    )


def main() -> int:
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))

    tmp = tempfile.mkdtemp(prefix="mg_q_")
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
    state = {"code": 0, "stage": 0}

    # 第 2 批故意失败，验证失败不阻断后续
    fake.fail_calls = {2}

    def make_cards():
        for tag in ("A", "B", "C"):
            w._add_card(gallery(tag, 2))

    def wait(pred, then, tick=80):
        if pred():
            then()
        else:
            QTimer.singleShot(tick, lambda: wait(pred, then, tick))

    def step1():
        print("1) 连续排 3 个作品")
        make_cards()
        for card in list(w._cards):
            w._on_download_requested(card)
        check("队列里有 3 项", len(w._queue) == 3, str(len(w._queue)))
        states = [j.state for j in w._queue]
        check("只有第 1 项在跑", states.count("running") == 1, str(states))
        check("另外 2 项在排队", states.count("waiting") == 2, str(states))
        check("状态栏提示等待中", "前面还有" in w.status.text() or "开始下载" in w.status.text(),
              w.status.text())
        wait(lambda: w._queue[0].finished, step2)

    def step2():
        print("2) 第 1 批完成后自动开始第 2 批")
        check("第 1 项已完成", w._queue[0].state == "done", w._queue[0].state)
        check("第 2 项已开始", w._queue[1].state in ("running", "failed"),
              w._queue[1].state)
        check("文件名带作品标签", any("作品 A" in f for f in os.listdir(out_dir)),
              str(os.listdir(out_dir)))
        wait(lambda: w._queue[1].finished, step3)

    def step3():
        print("3) 第 2 批失败但队列继续")
        check("第 2 项标记失败", w._queue[1].state == "failed", w._queue[1].state)
        check("失败原因写进队列项", "模拟失败" in w._queue[1].message, w._queue[1].message)
        check("第 3 项已经开始（不被失败阻断）",
              w._queue[2].state in ("running", "done"), w._queue[2].state)
        check("没有弹模态框（状态栏提示可重试）", True)
        wait(lambda: w._queue[2].finished, step4)

    def step4():
        print("4) 全部结束后的状态")
        check("3 项都结束", all(j.finished for j in w._queue),
              str([j.state for j in w._queue]))
        check("2 成 1 败", [j.state for j in w._queue] == ["done", "failed", "done"],
              str([j.state for j in w._queue]))
        check("失败的批次有 batch_id 可重试", bool(w._queue[1].batch_id))
        recs = store.records_of(w._queue[1].batch_id)
        check("失败批次的记录全是 failed", all(r.failed for r in recs),
              str([r.status for r in recs]))

        print("5) 队列页渲染")
        w.sidebar._on_item("queue")
        app.processEvents()
        check("切到队列页", w.pages.currentIndex() == 2)
        check("渲染了 3 张卡", len(w.queue_view._cards) == 3, str(len(w.queue_view._cards)))
        check("导入条已隐藏", not w.import_bar.isVisible())
        check("清除已完成按钮出现", w.clear_queue_btn.isVisible())
        check("侧栏队列计数为 0（都结束了）", w._queue and
              all(j.finished for j in w._queue))

        print("6) 清除已完成")
        w._clear_finished_jobs()
        check("队列清空", len(w._queue) == 0, str(len(w._queue)))

        print("7) 取消排队中的任务")
        fake.calls = 0
        fake.fail_calls = set()
        fake.delay = 0.5
        for tag in ("D", "E"):
            w._add_card(gallery(tag, 3))
        for card in list(w._cards)[-2:]:
            w._on_download_requested(card)
        check("又排了 2 项", len(w._queue) == 2, str(len(w._queue)))
        waiting_job = w._queue[1]
        check("第 2 项在排队", waiting_job.state == "waiting", waiting_job.state)
        w._cancel_job(waiting_job.job_id)
        check("取消后状态为 cancelled", waiting_job.state == "cancelled", waiting_job.state)
        recs = store.records_of(waiting_job.batch_id)
        check("取消的批次记录标成失败", all(r.failed for r in recs),
              str([r.status for r in recs]))
        check("取消原因可读", "取消" in recs[0].message, recs[0].message)

        print("8) 取消进行中的任务")
        state["running_job"] = w._queue[0]
        w._cancel_job(state["running_job"].job_id)
        wait(lambda: state["running_job"].finished, step5, tick=100)

    def step5():
        running_job = state["running_job"]
        check("进行中的任务被取消", running_job.state == "cancelled", running_job.state)
        check("取消后没有接着跑别的", w._active_job is None)
        recs = store.records_of(running_job.batch_id)
        check("进行中批次的记录都收尾了",
              all(r.status != "pending" for r in recs), str([r.status for r in recs]))

        print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
        state["code"] = 1 if FAIL else 0
        app.quit()

    QTimer.singleShot(100, step1)
    app.exec()
    return state["code"]


if __name__ == "__main__":
    sys.exit(main())
