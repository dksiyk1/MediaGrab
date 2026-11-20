"""分P 选集测试（需要联网，用一条 4P 的 B站 视频）。

验证：
- 适配器把完整 page_list 带出来
- ?p=N 能切到对应分P（标题带 _P2_ 之类的后缀）
- 「全部 N P 加入队列」把每个 P 都排进队列
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.core.downloaders.base import DownloadResult
from mediagrab.core.downloaders.manager import DownloadManager
from mediagrab.core.history import HistoryStore
from mediagrab.ui.main_window import MainWindow

# 4 个分P 的官方 PV
URL = "https://www.bilibili.com/video/BV1JYsd6sE4k"
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


class NoopManager(DownloadManager):
    """不真下载，只确认任务被建出来并入了队。

    run() 会卡在 gate 上：否则 4 个分P 解析要 6 秒、下载瞬间完成，
    等我们去检查时队列早就跑空了，看不到「1 个在跑 + N 个排队」这个关键状态。
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self.gate = threading.Event()

    def run(self, tasks, progress=None, mode=None, on_file_done=None):
        # 4 个分P 挨个解析要两分钟往上，gate 超时必须远大于它 ——
        # 一旦 gate 自己放行，「1 个在跑 + N 个排队」这个状态就抓不到了
        self.gate.wait(600)
        results = []
        for i, task in enumerate(tasks):
            res = DownloadResult(True, path=os.path.join(self.config.download_dir,
                                                         f"{task.filename}.{task.ext}"))
            results.append(res)
            if on_file_done:
                on_file_done(task, res)
        return results


def _bili_reachable(timeout: float = 4.0) -> bool:
    """先探一下 B站 接口通不通。

    这个用例会真的去请求合集接口。网络抖动时它会一直等到 run_tests 的
    900 秒超时 —— 一次全量回归从 3 分钟变成 18 分钟，还会报一堆看起来
    像代码 bug 的失败。探不通就直接跳过，别让环境问题伪装成回归失败。
    """
    try:
        socket.create_connection(("api.bilibili.com", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def main() -> int:
    if not _bili_reachable():
        print("跳过：连不上 api.bilibili.com（离线 / 代理 / 网络受限）。")
        print("      分 P 逻辑要在有正常网络的环境里验证。")
        return 0

    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))

    tmp = tempfile.mkdtemp(prefix="mg_pages_")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)

    store = HistoryStore(os.path.join(tmp, "history.db"))
    w = MainWindow()
    w.history.close()
    w.history = store
    w.history_view.store = store
    w.config.download_dir = out_dir
    w.config.prefer_speed = True          # 跳过 yt-dlp 找高清，测试只关心分P
    w.engine = w._make_engine()
    w.dm = NoopManager(w.config)
    w.show()

    state = {"code": 0}

    print("1) 解析合集首页")
    info = w.engine.parse_url(URL)
    pages = (info.extra or {}).get("page_list") or []
    check("拿到 page_list", len(pages) >= 2, str(len(pages)))
    check("条目字段齐全",
          all({"index", "part", "duration", "cid"} <= set(p) for p in pages),
          str(pages[0] if pages else {}))
    check("页码从 1 连续", [p["index"] for p in pages] == list(range(1, len(pages) + 1)),
          str([p["index"] for p in pages]))
    print(f"  共 {len(pages)} P：{pages[0].get('part')!r} …")

    print("2) ?p=N 切换")
    p2_url = w._page_url(info.source_url, 2)
    check("URL 挂上了 p=2", p2_url.endswith("?p=2") or p2_url.endswith("&p=2"), p2_url)
    check("已有 ? 时用 &", w._page_url("https://a.com/x?a=1", 3).endswith("&p=3"),
          w._page_url("https://a.com/x?a=1", 3))
    info2 = w.engine.parse_url(p2_url)
    check("P2 标题带 _P2_", "_P2_" in info2.title, info2.title)
    check("P2 与 P1 标题不同", info2.title != info.title, info2.title[:24])

    print("2.5) 换页就地替换卡片")
    w._add_card(info)
    first = w._cards[-1]
    before = len(w._cards)
    # 这条路径以前没人跑过：card_save_hint 漏 import 时一点「P2」就 NameError，
    # 而它又是 mixin 从 main_window 拆出来的，不能反向 import
    w._replace_card(first, info2, "", 2)
    check("卡片数不变", len(w._cards) == before, f"{before} → {len(w._cards)}")
    check("新卡片是 P2", w._cards[-1].info.title == info2.title,
          w._cards[-1].info.title[:24])
    check("旧卡片已移除", first not in w._cards)

    print("3) 卡片出现「共 N P」入口")
    w._add_card(info)
    card = w._cards[-1]
    check("卡片有 pages_btn", hasattr(card, "pages_btn"), "没有")
    check("按钮文案含 P 数", f"{len(pages)} P" in card.pages_btn.text(),
          card.pages_btn.text())

    print("4) 全部 N P 加入队列")
    w._enqueue_all_pages(card)

    def wait_done():
        if w._page_workers:
            QTimer.singleShot(300, wait_done)
            return
        after()

    def after():
        jobs = w._queue
        check("队列里每个 P 一个 job", len(jobs) == len(pages), f"{len(jobs)} vs {len(pages)}")
        titles = [j.title for j in jobs]
        check("标题各不相同（真的是不同 P）", len(set(titles)) == len(titles),
              str([t[:20] for t in titles[:4]]))
        check("每个 job 只有一个任务", all(j.total == 1 for j in jobs),
              str([j.total for j in jobs]))
        check("批次号各不相同", len({j.batch_id for j in jobs}) == len(jobs))
        check("历史里落了对应条数",
              store.stats()["batches"] == len(pages), str(store.stats()))

        # 下载被 gate 卡住，此刻正好能观察调度状态
        check("第一个正在跑", jobs[0].state == "running", jobs[0].state)
        rest = jobs[1:]
        check("其余都在排队（串行调度）", all(j.state == "waiting" for j in rest),
              str([j.state for j in rest]))

        print("5) 队列页渲染")
        w.sidebar._on_item("queue")
        app.processEvents()
        check("渲染出 N 张队列卡", len(w.queue_view._cards) == len(jobs),
              str(len(w.queue_view._cards)))

        print("6) 放行后队列逐个跑完")
        w.dm.gate.set()
        wait_all_done()

    def wait_all_done():
        if any(not j.finished for j in w._queue):
            QTimer.singleShot(200, wait_all_done)
            return
        check("全部完成", all(j.state == "done" for j in w._queue),
              str([j.state for j in w._queue]))
        check("历史里每条都成功了",
              all(r.ok for j in w._queue
                  for r in store.records_of(j.batch_id)),
              str(store.stats()))
        print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
        state["code"] = 1 if FAIL else 0
        app.quit()

    QTimer.singleShot(300, wait_done)
    app.exec()
    return state["code"]


if __name__ == "__main__":
    sys.exit(main())
