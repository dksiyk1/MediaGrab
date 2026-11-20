"""批量解析验证：一次粘贴多条链接，其中混一条坏链接。

验证卡片数量、进度条按条数推进、失败条目单独汇总而不是整体失败。
"""

import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from mediagrab.ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEXT = """https://www.bilibili.com/video/BV1pmxK6REXd
https://xhslink.cn/o/8c2s9ek7pDV
https://v.douyin.com/dcJJwUpngMI/
https://www.bilibili.com/video/BV0000000000"""

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name} {extra}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


def _bili_reachable(timeout: float = 4.0) -> bool:
    """先探一下 B站 接口通不通。

    批量解析用例里写的是真实 BV 号，网络不通时会挂到 run_tests 的 900 秒
    超时，把一次回归拖成十几分钟。探不通就跳过。
    """
    try:
        socket.create_connection(("api.bilibili.com", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def main() -> None:
    if not _bili_reachable():
        print("跳过：连不上 api.bilibili.com（离线 / 代理 / 网络受限）。")
        print("      批量解析要在有正常网络的环境里验证。")
        return

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    app.processEvents()

    w.input.setPlainText(TEXT)
    app.processEvents()

    state = {"t0": time.time(), "pcts": [], "done": False}

    def tick():
        if state["done"]:
            return
        state["pcts"].append(w.progress.value())
        if w._parse_worker is None or not w._parse_worker.isRunning():
            if w._batch_done >= w._batch_total and w._batch_total:
                state["done"] = True
                finish()
                return
        if time.time() - state["t0"] > 120:
            state["done"] = True
            print("  超时")
            finish()

    def finish():
        cost = time.time() - state["t0"]
        print(f"  耗时 {cost:.1f}s · 卡片 {len(w._cards)} 张 · 失败 {len(w._batch_errors)} 条")
        print(f"  进度采样 {state['pcts'][-10:]}")
        print(f"  状态栏：{w.status.text()}")

        check("粘 4 条解析出 3 张卡片", len(w._cards) == 3, f"got {len(w._cards)}")
        check("坏链接被单独记录", len(w._batch_errors) == 1, f"got {len(w._batch_errors)}")
        check("进度到达满值", w.progress.value() == w.progress.maximum(),
              f"{w.progress.value()}/{w.progress.maximum()}")
        check("进度单调不减", all(b >= a for a, b in zip(state["pcts"], state["pcts"][1:])))
        check("状态栏报告了失败数", "失败" in w.status.text(), w.status.text())

        # 三个平台各一张
        plats = sorted(c.info.platform for c in w._cards)
        check("平台覆盖 bilibili/douyin/xiaohongshu",
              plats == ["bilibili", "douyin", "xiaohongshu"], str(plats))

        out = os.path.join(ROOT, "_ui_shots", "06-batch.png")
        w.grab().save(out)
        print(f"  截图 {out}")

        # 阶段二：重试失败项，已解析的卡片必须保留
        print("  重试失败项…")
        before = len(w._cards)
        w._retry_failed()
        app.processEvents()
        check("重试时保留已有卡片", len(w._cards) == before,
              f"{len(w._cards)} vs {before}")
        check("输入框只剩失败的那条",
              len(w.input.toPlainText().strip().splitlines()) == 1,
              w.input.toPlainText().strip()[:40])
        state["retry_t0"] = time.time()

        def wait_retry():
            if w._parse_worker is not None and w._parse_worker.isRunning():
                QTimer.singleShot(400, wait_retry)
                return
            check("重试后卡片数不变", len(w._cards) == before, f"{len(w._cards)}")
            check("重试失败有明确反馈", "失败" in w.status.text(), w.status.text())
            w.grab().save(os.path.join(ROOT, "_ui_shots", "07-batch-retry.png"))
            print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
            app.quit()
            import sys as _s
            _s.exit(1 if FAIL else 0)

        QTimer.singleShot(400, wait_retry)
        return

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)

    w._start_parse()
    print(f"  粘贴 {len(TEXT.strip().splitlines())} 行，开始解析")
    app.exec()


if __name__ == "__main__":
    main()
