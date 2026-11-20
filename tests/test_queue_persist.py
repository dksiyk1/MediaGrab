"""下载队列持久化：退出后排队任务不丢，重启接着下。

跑法：python tests/test_queue_persist.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.downloaders.base import DownloadTask
from mediagrab.core.history import HistoryStore
from mediagrab.ui import queue_store
from mediagrab.ui.main_window import MainWindow
from mediagrab.ui.queue import QueueJob

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


def make_job(job_id="j1", state="waiting", n_tasks=2) -> QueueJob:
    tasks = [
        DownloadTask(
            url=f"http://127.0.0.1:1/{job_id}_{i}.mp4",
            output_dir=r"C:\tmp\mg",
            filename=f"{job_id}_{i}",
            ext="mp4",
            referer="https://www.douyin.com/",
            headers={"Cookie": "a=b"},
            page_url=f"https://www.douyin.com/video/{job_id}",
            format_selector="137+140",
            merge=True,
        )
        for i in range(n_tasks)
    ]
    return QueueJob(
        job_id=job_id,
        batch_id="b-" + job_id,
        title=f"作品 {job_id}",
        platform="douyin",
        kind="video",
        mode="internal",
        tasks=tasks,
        meta={"source_url": "https://v.douyin.com/x/", "author": "作者",
              "media_type": "video", "format_label": "1080P · MP4"},
        state=state,
    )


def test_save_load(tmp: Path):
    print("1) 存盘与读回")
    queue_store.QUEUE_PATH = tmp / "queue.json"

    queue_store.save([])
    check("空队列不建文件", not queue_store.QUEUE_PATH.exists())

    jobs = [make_job("a"), make_job("b", state="running"), make_job("c", state="done")]
    queue_store.save(jobs)
    check("只有 waiting 落盘", queue_store.QUEUE_PATH.exists())

    raw = json.loads(queue_store.QUEUE_PATH.read_text(encoding="utf-8"))
    check("文件里只有 1 个任务", len(raw["jobs"]) == 1, str(len(raw["jobs"])))
    check("存的是 waiting 那条", raw["jobs"][0]["job_id"] == "a", raw["jobs"][0]["job_id"])

    loaded = queue_store.load()
    check("读回 1 个任务", len(loaded) == 1, str(len(loaded)))
    job = loaded[0]
    check("任务数一致", len(job.tasks) == 2, str(len(job.tasks)))
    check("状态重置为 waiting", job.state == "waiting", job.state)
    t = job.tasks[0]
    check("headers 保留", t.headers.get("Cookie") == "a=b", str(t.headers))
    check("merge 保留", t.merge is True)
    check("format_selector 保留", t.format_selector == "137+140", t.format_selector)
    check("meta 保留", job.meta.get("format_label") == "1080P · MP4", str(job.meta))

    print("2) 异常输入")
    queue_store.QUEUE_PATH.write_text("{ 这不是 json", encoding="utf-8")
    check("损坏文件读回空列表", queue_store.load() == [])
    broken = list(tmp.glob("queue.broken-*"))
    check("损坏文件被改名留档", len(broken) == 1, str([p.name for p in broken]))
    check("原文件已不阻塞后续", not queue_store.QUEUE_PATH.exists())

    queue_store.QUEUE_PATH.write_text(
        json.dumps({"version": 1, "jobs": [
            {"job_id": "x", "tasks": [{"url": "", "output_dir": "d"}]},   # 缺 url
            {"job_id": "y", "tasks": [{"url": "http://a", "output_dir": "d"}]},
            {"job_id": "z"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = queue_store.load()
    check("坏任务被跳过、空任务被丢弃", [j.job_id for j in loaded] == ["y"],
          str([j.job_id for j in loaded]))

    print("3) 清空")
    queue_store.save([make_job("only_waiting")])
    check("有任务时文件在", queue_store.QUEUE_PATH.exists())
    queue_store.save([])
    check("队列空了文件被删", not queue_store.QUEUE_PATH.exists())
    queue_store.clear()


def test_restore(app, tmp: Path):
    print("4) 启动恢复")
    queue_store.QUEUE_PATH = tmp / "queue.json"
    queue_store.save([make_job("r1"), make_job("r2", n_tasks=1)])

    win = MainWindow(restore_queue=False)
    win.history = HistoryStore(str(tmp / "history.db"))
    started = []
    win._pump_queue = lambda: started.append(1)     # 只验证恢复，不真去下载
    win.show()
    pump(app, 150)

    win._restore_queue()
    pump(app, 200)

    check("恢复了 2 个任务", len(win._queue) == 2, str(len(win._queue)))
    check("状态都是 waiting", all(j.state == "waiting" for j in win._queue))
    check("恢复了就会继续排队", started, str(started))
    check("状态栏给出提示", "恢复" in win.status.text(), win.status.text())

    job = win._queue[0]
    check("重建了 batch_id", bool(job.batch_id), job.batch_id)
    check("重建了 record_ids", len(job.record_ids) == len(job.tasks),
          f"{len(job.record_ids)}/{len(job.tasks)}")
    recs = win.history.records_of(job.batch_id)
    check("历史里落成 pending", [r.status for r in recs] == ["pending"] * 2,
          str([r.status for r in recs]))
    check("历史记住了清晰度", recs[0].format_label == "1080P · MP4", recs[0].format_label)

    # 恢复后再存盘，不能让任务凭空少一条
    queue_store.save(win._queue)
    check("再次存盘仍是 2 个", len(queue_store.load()) == 2,
          str(len(queue_store.load())))

    win._queue.clear()
    win.close()
    pump(app, 200)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="mg_queue_"))
    original = queue_store.QUEUE_PATH
    try:
        test_save_load(tmp)
        app = QApplication(sys.argv)
        app.setFont(QFont("Microsoft YaHei UI", 9))
        test_restore(app, tmp)
    finally:
        queue_store.QUEUE_PATH = original
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
